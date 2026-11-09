"""Stagehand v4 bring-your-own-LLM callback for DeepSeek.

Stagehand v4's native model providers do not include DeepSeek, so we supply a
client-side generate callback. Stagehand passes a provider-neutral
``LLMStructuredGenerateParams`` (messages, system prompt, temperature, and a
JSON-schema response format); we forward it to DeepSeek's OpenAI-compatible
chat-completions endpoint and return a matching ``LLMStructuredGenerateResult``.

DeepSeek is text-only and supports JSON-object mode (not strict schema), so we
instruct the model to return a JSON object matching the requested schema and
parse the text. Stagehand validates the result against the schema it requested.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from stagehand import LLMStructuredGenerateResult

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def _block_text(block: Any) -> str:
    root = getattr(block, "root", block)
    if getattr(root, "type", None) == "text":
        return root.text or ""
    return ""


def _message_text(message: Any) -> str:
    content = message.content
    blocks = content if isinstance(content, list) else [content]
    return "\n".join(_block_text(b) for b in blocks).strip()


def _schema_json(response_format: Any) -> str:
    schema = getattr(response_format, "schema_", None)
    if schema is None:
        return "{}"
    try:
        return json.dumps(schema.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
    except Exception:  # pragma: no cover - schema serialization is best-effort
        return "{}"


def make_generate(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
):
    """Return an async Stagehand generate callback backed by DeepSeek."""
    api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
    base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
    model = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)

    if not api_key:
        raise ValueError(
            "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and fill it in."
        )

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(params) -> LLMStructuredGenerateResult:
        response_format = params.response_format
        if getattr(response_format, "type", None) != "json_schema":
            raise NotImplementedError(
                "DeepSeek callback only supports json_schema structured generation."
            )

        messages: list[dict[str, str]] = []
        if params.system_prompt:
            messages.append({"role": "system", "content": params.system_prompt})
        for message in params.messages:
            messages.append({"role": message.role.value, "content": _message_text(message)})

        messages.append(
            {
                "role": "user",
                "content": (
                    "Return a single JSON object matching this schema:\n"
                    f"{_schema_json(response_format)}"
                ),
            }
        )

        kwargs: dict[str, Any] = {}
        if params.temperature is not None:
            kwargs["temperature"] = params.temperature

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            **kwargs,
        )
        text = response.choices[0].message.content or "{}"
        return LLMStructuredGenerateResult.model_validate(
            {
                "role": "assistant",
                "content": {"type": "text", "text": text},
                "output_format": "json_schema",
                "structured_content": json.loads(text),
            }
        )

    return generate
