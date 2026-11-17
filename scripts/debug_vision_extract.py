"""Reproduce the PO-2026-0044 listing-extraction failure with full visibility.

Points the real Stagehand + OpenRouter/Qwen3-VL pipeline at a live search
page (price.com.hk 'laptop') using the production listing instruction, and
dumps exactly what the model receives (DOM snapshot head, screenshot size)
and what it answers. Usage:

    uv run python scripts/debug_vision_extract.py [url] [instruction]
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

from stagehand import Stagehand, local_browser

DEFAULT_URL = "https://www.price.com.hk/search.php?q=laptop"


class Product(BaseModel):
    product_name: str
    price_hkd: str | None = None
    description: str | None = None
    description_english: str | None = None


class ListingPage(BaseModel):
    products: list[Product]


async def main() -> None:
    from procurement.llm.stagehand_llm import make_generate
    from procurement.tools.suppliers import _listing_instruction

    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    instruction = sys.argv[2] if len(sys.argv) > 2 else _listing_instruction("laptop")

    base_gen = make_generate()

    async def generate(params):
        for i, message in enumerate(params.messages or []):
            content = message.content
            blocks = content if isinstance(content, list) else [content]
            for j, block in enumerate(blocks):
                root = getattr(block, "root", block)
                kind = getattr(root, "type", None)
                if kind == "text":
                    text = getattr(root, "text", "") or ""
                    print(f"--- MSG[{i}].block[{j}] text ({len(text)} chars) ---")
                    print(repr(text[:500]))
                elif kind == "image":
                    data = getattr(root, "data", None)
                    print(
                        f"--- MSG[{i}].block[{j}] image "
                        f"(data: {len(data) if data else 0} chars, "
                        f"mime: {getattr(root, 'mime_type', None)}) ---"
                    )
        try:
            result = await base_gen(params)
        except Exception:
            print("--- CALLBACK RAISED ---")
            traceback.print_exc()
            raise
        try:
            text = result.content.root.text
        except AttributeError:
            text = str(result.content)
        print(f"--- MODEL TEXT ({len(text or '')} chars) ---")
        print(repr((text or "")[:800]))
        return result

    generate.vision = True

    stagehand = await Stagehand.create(
        browser=await local_browser.launch(headless=False), model=generate
    )
    try:
        page = (await stagehand.browser.context.pages())[0]
        await page.goto(url)
        # Same Cloudflare wait the production browser tool uses.
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
            title = (await page.title()).lower()
            if "just a moment" not in title:
                break
            await page.wait_for_timeout(2000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(6000)
        print("--- PAGE TITLE ---")
        print(await page.title())
        result = await stagehand.extract(instruction, ListingPage, screenshot=True)
        print("--- EXTRACT RESULT ---")
        print(result.data)
    finally:
        await stagehand.close()


if __name__ == "__main__":
    asyncio.run(main())