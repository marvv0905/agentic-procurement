"""Browser tool stub.

Wraps Stagehand's discrete ``observe`` / ``act`` / ``extract`` tools. Stagehand
v4 leaves control flow to the application; this stub owns the typed boundary so
the controller never imports the Stagehand SDK directly.
"""

from __future__ import annotations

from typing import Any, Protocol


class BrowserTool(Protocol):
    def observe(self, url: str) -> dict[str, Any]: ...

    def act(self, action: dict[str, Any]) -> dict[str, Any]: ...

    def extract(self, schema: dict[str, Any]) -> dict[str, Any]: ...


class StagehandBrowser:
    """Placeholder implementation.

    Raising ``NotImplementedError`` is intentional until the Phase 1 feasibility
    spike confirms a pinned Stagehand/Python configuration and local Chromium.
    """

    def __init__(self, **_: Any) -> None:
        raise NotImplementedError("Stagehand is not installed yet (Phase 1 spike).")

    def observe(self, url: str) -> dict[str, Any]:
        raise NotImplementedError("Stagehand is not installed yet (Phase 1 spike).")

    def act(self, action: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Stagehand is not installed yet (Phase 1 spike).")

    def extract(self, schema: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Stagehand is not installed yet (Phase 1 spike).")
