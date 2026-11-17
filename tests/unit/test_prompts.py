"""Prompt structure (Lab 2): system/user split, few-shot examples, and the
adapter's chat-style structured call."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from procurement.llm.prompts import (
    PROMPT_VERSION,
    build_request_messages,
    build_request_prompt,
)


def test_prompt_version_is_current():
    assert PROMPT_VERSION == "v8"


def test_request_messages_are_system_then_user():
    messages = build_request_messages("I need a laptop for my team", None)
    assert [m["role"] for m in messages] == ["system", "user"]


def test_system_message_carries_persona_catalog_rules_and_examples():
    system = build_request_messages("hello", None)[0]["content"]
    assert system.startswith("You are the intake assistant")
    # Catalog is injected and stays in sync with CATEGORIES.
    assert "monitor" in system
    # The "other" distillation contract is in the system message.
    assert "search_query" in system
    # Few-shot examples (Lab 2 §2.3) for the error-prone distillation step.
    assert "Examples of the" in system
    assert "Thinkpad 512gb laptop" in system


def test_user_message_carries_draft_and_latest_text():
    messages = build_request_messages("make it 5 laptops", None)
    user = messages[1]["content"]
    assert "make it 5 laptops" in user
    assert "{}" in user  # empty draft serializes as an empty object


def test_user_message_carries_existing_draft():
    from procurement.schemas import RequestDraft

    draft = RequestDraft(
        category="other",
        quantity=3,
        attributes={"item": "laptop", "keywords": ["Thinkpad"]},
        warranty_months_min=None,
        max_total_hkd="20000",
        deadline=None,
        department=None,
        preferences={},
        out_of_scope=[],
    )
    user = build_request_messages("actually 5 laptops", draft)[1]["content"]
    assert "Thinkpad" in user
    assert "actually 5 laptops" in user


def test_flat_prompt_joins_both_messages():
    prompt = build_request_prompt("I need a laptop", None)
    assert "You are the intake assistant" in prompt
    assert "I need a laptop" in prompt


# --- adapter: chat-style structured call ----------------------------------------


class _FakeCompletions:
    def __init__(self):
        self.captured = None

    async def create(self, **kwargs):
        self.captured = kwargs
        message = type("M", (), {"content": '{"ok": true}'})()
        choice = type("C", (), {"message": message})()
        return type("R", (), {"choices": [choice]})()


class _FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions()


def test_structured_messages_appends_schema_to_last_user():
    from procurement.llm.adapter import ConfigurableLLM

    llm = ConfigurableLLM(api_key="test", base_url="http://localhost:9")
    fake = _FakeClient()
    llm._client = fake

    import asyncio

    schema = {"type": "object", "properties": {"ok": {}}}
    messages = [
        {"role": "system", "content": "You are the intake assistant."},
        {"role": "user", "content": "User's latest message."},
    ]
    result = asyncio.run(llm.structured_messages(messages, schema))

    assert result == {"ok": True}
    sent = fake.chat.completions.captured["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[0]["content"] == "You are the intake assistant."
    assert sent[1]["content"].startswith("User's latest message.")
    assert "json" in sent[1]["content"].lower()
    assert json.dumps(schema) in sent[1]["content"]


def test_structured_messages_without_user_message_appends_one():
    from procurement.llm.adapter import ConfigurableLLM

    llm = ConfigurableLLM(api_key="test", base_url="http://localhost:9")
    fake = _FakeClient()
    llm._client = fake

    import asyncio

    asyncio.run(
        llm.structured_messages(
            [{"role": "system", "content": "sys"}], {"type": "object"}
        )
    )
    sent = fake.chat.completions.captured["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[1]["content"].startswith("Return a single JSON object")