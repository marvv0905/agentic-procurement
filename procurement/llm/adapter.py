"""Configurable LLM adapter (DeepSeek via the OpenAI-compatible API).

DeepSeek exposes an OpenAI-compatible chat-completions endpoint, so we drive it
with ``AsyncOpenAI`` pointed at ``DEEPSEEK_BASE_URL``. The provider/model are
configurable via environment so no provider-specific business logic leaks into
the rules engine or controller.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from ..schemas import RequestDraft
from .prompts import PROMPT_VERSION, build_request_prompt

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class ConfigurableLLM:
    """Async DeepSeek-backed adapter.

    ``structured`` uses DeepSeek's JSON-object mode and asks the model to return
    a JSON object matching ``schema``. It is used for parsing ambiguity and
    generating explanations, never for arithmetic or state transitions.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.prompt_version = PROMPT_VERSION

        if not self.api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def complete(self, prompt: str, **options: Any) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            **options,
        )
        return response.choices[0].message.content or ""

    async def structured(
        self, prompt: str, schema: dict[str, Any], **options: Any
    ) -> dict[str, Any]:
        instruction = (
            f"{prompt}\n\n"
            "Return a single JSON object matching this schema:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": instruction}],
            response_format={"type": "json_object"},
            **options,
        )
        return json.loads(response.choices[0].message.content or "{}")

    async def update_draft(
        self, text: str, current_draft: RequestDraft | None = None
    ) -> RequestDraft:
        """Parse the latest message into an updated ``RequestDraft``.

        ``current_draft`` carries forward already-confirmed fields, so follow-up
        messages (corrections, answers to clarifications) merge instead of
        starting over.
        """
        prompt = build_request_prompt(text, current_draft)
        data = await self.structured(prompt, RequestDraft.model_json_schema())
        return RequestDraft.model_validate(data)
