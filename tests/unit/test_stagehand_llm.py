"""Unit tests for the Stagehand generate callback (model routing, payloads)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from procurement.llm.stagehand_llm import (
    _message_content,
    _normalize_base_url,
    _resolve_config,
    make_generate,
)

ENV_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


class _Role:
    def __init__(self, value: str) -> None:
        self.value = value


class _Message:
    def __init__(self, content) -> None:
        self.content = content
        self.role = _Role("user")


# ---- _message_content -----------------------------------------------------


def test_text_only_message_collapses_to_string():
    message = _Message([{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}])
    assert _message_content(message) == "hello\nworld"


def test_image_block_becomes_data_url_part():
    message = _Message(
        [
            {"type": "text", "text": "find the add-to-cart button"},
            {"type": "image", "data": "abc123", "mime_type": "image/jpeg"},
        ]
    )
    content = _message_content(message)
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "find the add-to-cart button"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,abc123"},
    }


def test_object_blocks_are_supported():
    """Stagehand may hand over pydantic-wrapped blocks instead of dicts."""
    text_block = SimpleNamespace(root=SimpleNamespace(type="text", text="hi"))
    image_block = SimpleNamespace(
        root=SimpleNamespace(type="image", data="xyz", mime_type="image/png")
    )
    content = _message_content(_Message([text_block, image_block]))
    assert content == [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz"}},
    ]


def test_image_block_without_data_is_dropped():
    message = _Message([{"type": "text", "text": "page"}, {"type": "image", "mime_type": "image/jpeg"}])
    assert _message_content(message) == "page"


# ---- _normalize_base_url --------------------------------------------------


def test_bare_openrouter_url_gets_api_v1_suffix():
    assert _normalize_base_url("https://openrouter.ai") == "https://openrouter.ai/api/v1"
    assert _normalize_base_url("https://openrouter.ai/") == "https://openrouter.ai/api/v1"


def test_versioned_openrouter_url_is_untouched():
    assert _normalize_base_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1"


def test_deepseek_url_is_untouched():
    assert _normalize_base_url("https://api.deepseek.com") == "https://api.deepseek.com"


# ---- _resolve_config ------------------------------------------------------


def test_openrouter_env_is_preferred_and_flagged_as_vision(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3-vl-235b-a22b-instruct")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")

    api_key, base_url, model, vision = _resolve_config(None, None, None)
    assert api_key == "or-key"
    assert base_url == "https://openrouter.ai/api/v1"
    assert model == "qwen/qwen3-vl-235b-a22b-instruct"
    assert vision is True


def test_deepseek_fallback_is_text_only(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    api_key, base_url, model, vision = _resolve_config(None, None, None)
    assert api_key == "ds-key"
    assert base_url == "https://api.deepseek.com"
    assert model == "deepseek-chat"
    assert vision is False


def test_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3-vl-235b-a22b-instruct")

    api_key, base_url, model, vision = _resolve_config(
        "ds-key", "https://api.deepseek.com", "deepseek-chat"
    )
    assert api_key == "ds-key"
    assert model == "deepseek-chat"
    assert vision is False


def test_no_credentials_raises():
    with pytest.raises(ValueError, match="credentials"):
        make_generate()


def test_make_generate_exposes_vision_flag(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3-vl-235b-a22b-instruct")
    generate = make_generate()
    assert generate.vision is True

    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    generate = make_generate()
    assert generate.vision is False


# ---- per-operation routing (extraction -> text model) ------------------------


def test_extraction_routes_to_text_model_when_deepseek_configured(monkeypatch):
    """Vision primary + DeepSeek available: extraction calls carry
    extract_vision=False so no screenshot is attached, and the extraction
    system prompt routes to the text client (PO-2026-0044: the vision model
    returned empty lists on ~120k-char live DOMs)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3-vl-235b-a22b-instruct")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    generate = make_generate()
    assert generate.vision is True
    assert generate.extract_vision is False


def test_extract_vision_follows_primary_without_deepseek(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3-vl-235b-a22b-instruct")
    generate = make_generate()
    assert generate.extract_vision is True


def test_extract_vision_matches_deepseek_only_setup(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    generate = make_generate()
    assert generate.vision is False
    assert generate.extract_vision is False


def test_generate_routes_extraction_prompt_to_text_model(monkeypatch):
    """End-to-end routing inside the callback: the extraction system prompt
    hits the DeepSeek client; other prompts hit the primary vision client."""
    import procurement.llm.stagehand_llm as mod

    calls: list[tuple[str, str]] = []  # (api_key, model) per create() call

    class _Completions:
        def __init__(self) -> None:
            self.owner: _Client | None = None

        async def create(self, **kwargs):
            assert self.owner is not None
            calls.append((self.owner.key, kwargs["model"]))
            message = SimpleNamespace(content='{"ok": true}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class _Client:
        def __init__(self, api_key=None, base_url=None):
            self.key = api_key
            completions = _Completions()
            completions.owner = self
            self.chat = SimpleNamespace(completions=completions)

    monkeypatch.setattr(mod, "AsyncOpenAI", _Client)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3-vl-235b-a22b-instruct")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")

    generate = make_generate()

    def params(system_prompt: str):
        response_format = SimpleNamespace(
            type="json_schema",
            model_dump=lambda **kw: {"type": "json_schema", "name": "T", "schema": {}},
        )
        return SimpleNamespace(
            system_prompt=system_prompt,
            messages=[_Message("hello")],
            response_format=response_format,
            temperature=None,
        )

    import asyncio

    extraction_prompt = (
        "You are extracting content on behalf of a user. You will be given "
        "an instruction and DOM elements."
    )
    other_prompt = "You are an AI assistant evaluating progress of a task."

    asyncio.run(generate(params(extraction_prompt)))
    asyncio.run(generate(params(other_prompt)))

    assert calls == [
        ("ds-key", "deepseek-chat"),
        ("or-key", "qwen/qwen3-vl-235b-a22b-instruct"),
    ]