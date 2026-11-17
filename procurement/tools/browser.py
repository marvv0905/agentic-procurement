"""Browser tool backed by Stagehand v4 + a bring-your-own-LLM callback.

Model routing lives in ``procurement/llm/stagehand_llm.py``: OpenRouter's
vision model (Qwen3-VL) is used for the browser agent when configured, with
DeepSeek as the text-only fallback. When the resolved model is vision-capable,
``observe`` / ``act`` request page screenshots so the model sees the page
instead of only its DOM text; ``extract`` requests screenshots only when the
extraction route is vision-capable (by default extraction routes to the
text model, which handles large live-site DOMs reliably).

Wraps Stagehand's discrete ``observe`` / ``act`` / ``extract`` primitives and
drives a local Chrome window. Control flow stays in the application (the
controller calls these methods); Stagehand supplies the hands, not the brain.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, TypeVar

from stagehand import Stagehand, local_browser

from .. import frames
from ..llm.stagehand_llm import make_generate

T = TypeVar("T")


class BrowserTool(Protocol):
    async def observe(self, instruction: str) -> Any: ...

    async def act(self, instruction: str) -> Any: ...

    async def extract(self, instruction: str, schema: type[T]) -> T: ...


class StagehandBrowser:
    """Async wrapper around a local Stagehand session.

    Usage::

        browser = StagehandBrowser(headless=False)
        await browser.start()
        try:
            await browser.goto("http://127.0.0.1:8100/supplier/SUP_A/catalog")
            result = await browser.extract("extract the monitors", MonitorInfo)
        finally:
            await browser.close()
    """

    def __init__(
        self,
        headless: bool = False,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        order_id: int | None = None,
    ) -> None:
        self._headless = headless
        self._order_id = order_id
        self._generate = make_generate(api_key=api_key, base_url=base_url, model=model)
        # Vision models get screenshot observations; text-only models must not
        # (Stagehand would attach image blocks the model cannot read). The
        # extract_vision flag is False when extraction is routed to a
        # text-only model (stagehand_llm.make_generate).
        self._vision = bool(getattr(self._generate, "vision", False))
        self._extract_vision = bool(getattr(self._generate, "extract_vision", self._vision))
        self._browser = None
        self._stagehand: Stagehand | None = None

    async def start(self) -> None:
        self._browser = await local_browser.launch(headless=self._headless)
        self._stagehand = await Stagehand.create(
            browser=self._browser, model=self._generate
        )

    async def close(self) -> None:
        if self._stagehand is not None:
            await self._stagehand.close()
            self._stagehand = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None

    async def goto(self, url: str) -> Any:
        page = (await self._browser.context.pages())[0]
        await page.goto(url)
        await self._snap()
        return page

    async def evaluate(self, expression: str) -> Any:
        """Run JavaScript against the active page (deterministic DOM reads).

        ``expression`` must be a STRING expression (e.g. an immediately-
        invoked function, not an arrow-function literal): Stagehand's page
        wrapper silently returns ``{}`` for function-literal expressions.
        """
        page = (await self._browser.context.pages())[0]
        return await page.evaluate(expression)

    async def page_title(self) -> str:
        """Best-effort active page title ("" when unreadable).

        Used by the research flow's landing check: a title that doesn't
        mention the product means the click missed, so nothing on the page
        may be attributed to it.
        """
        try:
            page = (await self._browser.context.pages())[0]
            return (await page.title()) or ""
        except Exception:
            return ""

    async def _snap(self) -> None:
        """Best-effort capture for the live agent-view panel; never raises.

        With no order attached (smoke tests, manual scripts) this is a no-op.
        A screenshot is a UI nicety, so any failure is swallowed — the research
        run itself must never be at risk.
        """
        if self._order_id is None or self._browser is None:
            return
        try:
            page = (await self._browser.context.pages())[0]
            data = await page.screenshot(type="jpeg", quality=60)
        except Exception:
            return
        frames.put(self._order_id, data)

    async def click_link_containing(self, text: str) -> bool:
        """Deterministically click the first link whose text contains ``text``.

        LLM ``act`` navigation is unreliable with bring-your-own-LLM callbacks
        (the model may return no actionable element), so navigation uses the DOM
        when possible. Implemented via ``evaluate`` because Stagehand's Page
        does not proxy Playwright locators.
        """
        page = (await self._browser.context.pages())[0]
        try:
            clicked = await page.evaluate(
                """(text) => {
                    const wanted = text.toLowerCase();
                    const links = Array.from(document.querySelectorAll('a'));
                    const el = links.find(
                        (a) => (a.textContent || '').toLowerCase().includes(wanted)
                    );
                    if (el) { el.click(); return true; }
                    return false;
                }""",
                text,
            )
            if clicked:
                await page.wait_for_load_state("domcontentloaded")
                await self._snap()
            return bool(clicked)
        except Exception:
            return False

    async def click_link_to(self, description: str) -> bool:
        """Click a link identified by description, no matter the site layout.

        Cheap deterministic DOM click on the link text first; when that fails,
        ``observe`` (which works with bring-your-own-LLM callbacks, unlike
        ``act``) resolves the description to a selector, and the click itself
        stays deterministic.
        """
        if await self.click_link_containing(description):
            return True
        try:
            observed = await self.observe(
                f"find the link that opens the product page for {description}. "
                "Return the first matching link."
            )
        except Exception:
            return False
        actions = getattr(observed, "data", None) if observed else None
        selector = getattr(actions[0], "selector", None) if actions else None
        if not selector:
            return False
        try:
            page = (await self._browser.context.pages())[0]
            loc = page.locator(selector)
            if await loc.count() == 0:
                return False
            # NB: Stagehand's Locator.first is a method, not a property.
            await loc.nth(0).click()
            await page.wait_for_load_state("domcontentloaded")
            await self._snap()
            return True
        except Exception:
            return False

    async def go_back(self) -> None:
        """Navigate back one step in history (best effort)."""
        page = (await self._browser.context.pages())[0]
        try:
            await page.go_back()
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

    async def settle(self, seconds: float) -> None:
        """Idle wait for the page to finish settling (challenge remnants, hydration)."""
        page = (await self._browser.context.pages())[0]
        try:
            await page.wait_for_timeout(int(seconds * 1000))
        except Exception:
            pass

    async def wait_until_ready(self, max_seconds: float = 25.0) -> None:
        """Wait for a bot-protection challenge page to auto-resolve.

        Live aggregator sites (Cloudflare's "Just a moment…") auto-pass in a
        visible browser, but the time varies run to run — observed 2-25s.
        Extraction before it resolves reads the challenge page and finds
        nothing. Best effort — never raises.
        """
        page = (await self._browser.context.pages())[0]
        try:
            deadline = time.monotonic() + max_seconds
            while time.monotonic() < deadline:
                title = (await page.title()).lower()
                if "just a moment" not in title:
                    break
                await page.wait_for_timeout(2000)
        except Exception:
            pass
        await self._snap()

    async def observe(self, instruction: str) -> Any:
        kwargs = {"screenshot": True} if self._vision else {}
        return await self._stagehand.observe(instruction, **kwargs)

    async def act(self, instruction: str) -> Any:
        kwargs = {"screenshot": True} if self._vision else {}
        return await self._stagehand.act(instruction, **kwargs)

    async def extract(self, instruction: str, schema: type[T]) -> T:
        await self._snap()  # show the page the model is about to read
        # Extraction may be routed to a text-only model (large DOMs defeat
        # the vision model — see stagehand_llm); never attach screenshots it
        # cannot read. The extract_vision flag says whether it can.
        kwargs = {"screenshot": True} if self._extract_vision else {}
        result = await self._stagehand.extract(instruction, schema, **kwargs)
        return result.data
