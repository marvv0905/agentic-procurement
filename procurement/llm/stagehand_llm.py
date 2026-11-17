"""Stagehand v4 bring-your-own-LLM generate callback.

Model routing (first match wins):

1. Explicit ``api_key`` / ``base_url`` / ``model`` arguments.
2. OpenRouter env vars (``OPENROUTER_API_KEY`` / ``OPENROUTER_BASE_URL`` /
   ``OPENROUTER_MODEL``) — used for the browser agent's vision model (e.g.
   ``qwen/qwen3-vl-235b-a22b-instruct``) so Stagehand observations can include
   page screenshots.
3. DeepSeek env vars (text-only fallback; screenshots are never requested).

Both providers expose OpenAI-compatible chat-completions endpoints. Neither
supports strict schema mode, so we instruct the model to return a JSON object
matching the requested schema and parse the text; Stagehand validates the
result against the schema it requested.

Per-operation routing: extraction calls carry the page's full DOM (live
search pages reach ~120k chars), and vision models return empty lists at
that scale (PO-2026-0044: every listing extraction returned zero products),
while DeepSeek extracts reliably. So when DeepSeek credentials exist, calls
whose system prompt carries Stagehand's extraction signature route to
DeepSeek (text-only); observe/act keep the vision model for
screenshot-grounded interaction.

The returned callback carries a ``vision`` attribute (True when the resolved
model name suggests image support) so callers can decide whether to ask
Stagehand for screenshot observations, and an ``extract_vision`` attribute
for the same decision on extraction calls (False when extraction is routed
to the text model — screenshots must not be attached there).
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import AsyncOpenAI

from stagehand import LLMStructuredGenerateResult

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_VISION_MARKERS = ("vl", "vision")

#: Stagehand's extract() system prompt starts with this signature — the
#: router keys on it to send extraction calls to the text model.
_EXTRACTION_PROMPT_MARKER = "extracting content on behalf of a user"


def _block_field(block: Any, name: str, default: Any = None) -> Any:
    """Read a field from a content block that may be a dict or an object."""
    if isinstance(block, dict):
        return block.get(name, default)
    root = getattr(block, "root", block)
    value = getattr(root, name, default)
    return default if value is None else value


def _message_content(message: Any) -> str | list[dict[str, Any]]:
    """Convert a Stagehand message body to an OpenAI-compatible payload.

    Text blocks become text parts; image blocks (screenshots Stagehand attaches
    when the caller requests ``screenshot=True``) become ``image_url``
    data-URL parts. Text-only messages collapse to a plain string so
    non-vision calls stay cheap.
    """
    content = message.content
    blocks = content if isinstance(content, list) else [content]
    parts: list[dict[str, Any]] = []
    for block in blocks:
        kind = _block_field(block, "type")
        if kind == "text":
            parts.append({"type": "text", "text": _block_field(block, "text", "") or ""})
        elif kind == "image":
            data = _block_field(block, "data")
            mime = _block_field(block, "mime_type", "image/jpeg") or "image/jpeg"
            if data:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"},
                    }
                )
    if all(part["type"] == "text" for part in parts):
        return "\n".join(part["text"] for part in parts).strip()
    return parts


def _normalize_base_url(base_url: str) -> str:
    """Ensure OpenRouter base URLs carry the ``/api/v1`` prefix.

    The OpenAI SDK appends ``/chat/completions`` to ``base_url``, so a bare
    ``https://openrouter.ai`` (as written in many env files) must be expanded
    to ``https://openrouter.ai/api/v1``.
    """
    if "openrouter.ai" in base_url and not base_url.rstrip("/").endswith("/api/v1"):
        return base_url.rstrip("/") + "/api/v1"
    return base_url.rstrip("/")


def _resolve_config(
    api_key: str | None, base_url: str | None, model: str | None
) -> tuple[str | None, str, str, bool]:
    """Resolve provider config, preferring OpenRouter (vision) over DeepSeek.

    Returns ``(api_key, base_url, model, vision)``.
    """
    if os.getenv("OPENROUTER_API_KEY"):
        api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        base_url = _normalize_base_url(
            base_url or os.getenv("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)
        )
        model = model or os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL
    else:
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
        model = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    vision = any(marker in (model or "").lower() for marker in _VISION_MARKERS)
    return api_key, base_url, model, vision


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
    """Return an async Stagehand generate callback backed by an OpenAI-compatible API."""
    api_key, base_url, model, vision = _resolve_config(api_key, base_url, model)

    if not api_key:
        raise ValueError(
            "No LLM credentials found. Set OPENROUTER_API_KEY (vision model, "
            "preferred for the browser agent) or DEEPSEEK_API_KEY. "
            "Copy example.env to .env and fill it in."
        )

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    # Extraction calls carry the full page DOM (live search pages reach
    # ~120k chars) and the vision model returns empty lists at that scale
    # (PO-2026-0044: every listing extraction came back with zero products).
    # DeepSeek has extracted reliably from those DOMs all along, so when it
    # is configured, extraction calls route there while observe/act keep the
    # vision model for screenshot-grounded interaction.
    extract_client = client
    extract_model = model
    extract_vision = vision
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key and (model or "").lower().startswith("deepseek") is False:
        extract_client = AsyncOpenAI(
            api_key=deepseek_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        )
        extract_model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
        extract_vision = False

    async def generate(params) -> LLMStructuredGenerateResult:
        response_format = params.response_format
        if getattr(response_format, "type", None) != "json_schema":
            raise NotImplementedError(
                "Generate callback only supports json_schema structured generation."
            )

        # Route by Stagehand's operation prompt signature: extraction to
        # the text model, interaction to the primary (vision) model.
        use_extract_route = (
            extract_client is not client
            and bool(params.system_prompt)
            and _EXTRACTION_PROMPT_MARKER in params.system_prompt
        )
        active_client = extract_client if use_extract_route else client
        active_model = extract_model if use_extract_route else model

        messages: list[dict[str, Any]] = []
        if params.system_prompt:
            messages.append({"role": "system", "content": params.system_prompt})
        for message in params.messages:
            messages.append(
                {"role": message.role.value, "content": _message_content(message)}
            )
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

        try:
            response = await active_client.chat.completions.create(
                model=active_model,
                messages=messages,
                response_format={"type": "json_object"},
                **kwargs,
            )
        except Exception as exc:
            # Some OpenRouter models reject response_format; retry once without it.
            # The JSON-schema instruction is still in the message text.
            if "response_format" not in str(exc).lower():
                raise
            response = await active_client.chat.completions.create(
                model=active_model,
                messages=messages,
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

    generate.vision = vision  # type: ignore[attr-defined]
    # Whether EXTRACTION calls can take screenshots (they are text-routed to
    # DeepSeek when configured, so no images must be attached there).
    generate.extract_vision = extract_vision  # type: ignore[attr-defined]
    return generate