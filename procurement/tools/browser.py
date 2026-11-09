"""Browser tool backed by Stagehand v4 + a DeepSeek generate callback.

Wraps Stagehand's discrete ``observe`` / ``act`` / ``extract`` primitives and
drives a local Chrome window. Control flow stays in the application (the
controller calls these methods); Stagehand supplies the hands, not the brain.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from stagehand import Stagehand, local_browser

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
    ) -> None:
        self._headless = headless
        self._generate = make_generate(api_key=api_key, base_url=base_url, model=model)
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
        return page

    async def observe(self, instruction: str) -> Any:
        return await self._stagehand.observe(instruction)

    async def act(self, instruction: str) -> Any:
        return await self._stagehand.act(instruction)

    async def extract(self, instruction: str, schema: type[T]) -> T:
        result = await self._stagehand.extract(instruction, schema)
        return result.data
