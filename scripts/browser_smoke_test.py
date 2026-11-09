"""Browser smoke test (Phase 1 feasibility spike).

Proves the Stagehand + local Chrome + DeepSeek chain works end to end.

Usage:
    uv run python scripts/browser_smoke_test.py --target public
    uv run python scripts/browser_smoke_test.py --target local

``public`` navigates to example.com and extracts one field (isolates "is
Stagehand + Chrome + DeepSeek working?" from "is my mock site working?").

``local`` navigates to the mock supplier catalogue and extracts a typed monitor,
proving the agent can read our synthetic sites (the actual Phase 1 deliverable).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from procurement.tools.browser import StagehandBrowser  # noqa: E402


class PageHeading(BaseModel):
    heading: str


class MonitorInfo(BaseModel):
    model_name: str
    screen_inches: int
    price_hkd: str
    available_units: int


MOCK_BASE_URL = os.getenv("MOCK_BASE_URL", "http://127.0.0.1:8100")


async def run_public() -> None:
    browser = StagehandBrowser(headless=False)
    await browser.start()
    try:
        await browser.goto("https://example.com")
        result = await browser.extract(
            "extract the main heading of the page", PageHeading
        )
        print("PUBLIC extract result:", result)
    finally:
        await browser.close()


async def run_local() -> None:
    browser = StagehandBrowser(headless=False)
    await browser.start()
    try:
        url = f"{MOCK_BASE_URL}/supplier/SUP_A/catalog"
        await browser.goto(url)
        result = await browser.extract(
            "extract the first monitor listed on the page (model name, screen "
            "size in inches, unit price, and units in stock)",
            MonitorInfo,
        )
        print("LOCAL extract result:", result)
    finally:
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stagehand browser smoke test")
    parser.add_argument(
        "--target", choices=["public", "local"], default="local",
        help="public page sanity check, or the local mock supplier catalogue",
    )
    args = parser.parse_args()

    fn = run_public if args.target == "public" else run_local
    asyncio.run(fn())


if __name__ == "__main__":
    main()
