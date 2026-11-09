"""Configurable LLM adapter (stub).

The provider is configurable so no provider-specific business logic leaks into
the rules engine or controller. Exact supported model names are validated
during the feasibility spike.
"""

from __future__ import annotations

from typing import Any, Protocol

from .prompts import PROMPT_VERSION


class LLMAdapter(Protocol):
    def complete(self, prompt: str, **options: Any) -> str: ...

    def structured(self, prompt: str, schema: dict[str, Any], **options: Any) -> dict[str, Any]: ...


class ConfigurableLLM:
    """Placeholder that reads provider/model from environment.

    Not wired to a real provider until the Phase 1 feasibility spike.
    """

    def __init__(self, provider: str | None = None, model: str | None = None) -> None:
        self.provider = provider
        self.model = model
        self.prompt_version = PROMPT_VERSION

    def complete(self, prompt: str, **options: Any) -> str:
        raise NotImplementedError(
            f"LLM provider {self.provider!r} is not configured yet (Phase 1 spike)."
        )

    def structured(self, prompt: str, schema: dict[str, Any], **options: Any) -> dict[str, Any]:
        raise NotImplementedError(
            f"LLM provider {self.provider!r} is not configured yet (Phase 1 spike)."
        )
