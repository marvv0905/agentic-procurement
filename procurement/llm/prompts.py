"""Versioned prompts.

Prompts are versioned so evaluation runs are reproducible. The request-parsing
prompt is built dynamically from the category catalog so it stays in sync with
the attributes the rules engine actually validates.

Structure follows the chat-template pattern from Lab 2: a *system* message
carries the persona, the category catalog, the output contract, the rules and
a few distillation examples (few-shot); the *user* message carries only the
run-specific state — the current draft and the latest message. The schema
instruction is appended by the adapter when the structured call is made.
"""

from __future__ import annotations

import json
from typing import Any

from ..catalog import CATEGORIES
from ..schemas import RequestDraft

PROMPT_VERSION = "v8"


def _category_summary() -> str:
    lines = []
    for cid, spec in CATEGORIES.items():
        attrs = ", ".join(
            f"{a.name} ({a.type}"
            + (f", one of {list(a.choices)}" if a.choices else "")
            + ")"
            for a in spec.attributes
        )
        lines.append(f"- {cid}: {spec.label} — attributes: {attrs}")
    return "\n".join(lines)


_REQUEST_SYSTEM_TEMPLATE = """\
You are the intake assistant for a company procurement system. You convert a
conversation into a structured purchase request.

Available product categories and their attributes:
{categories}

There is also a free-text category "other": use it when the user asks for a
product that is NOT one of the categories above (it can be anything: a printer,
a desk lamp, headphones…). For "other" set attributes to:
- item: the product name alone, e.g. "laptop" or "noise cancelling
  headphones" — never the full sentence, who it's for, or quantity phrases
  ("Laptops for my team" -> "laptop")
- keywords: a list of the mandatory requirements the user stated, as short
  searchable phrases, e.g. ["noise cancelling", "bluetooth", "under 200g"]
- search_query: a concise shopping-search query (3-6 words) distilled from the
  item and its keywords — the exact phrase you would type into a retailer's
  search box, with no duplicated words. Keep the product type plus the most
  distinctive brand/model/requirement (e.g. for item "laptop" with keywords
  ["Thinkpad", "512gb"]: "Thinkpad 512gb laptop"). Never add requirements the
  user did not state.
Keep using out_of_scope only for things that are not products at all (e.g.
rubber ducks, coffee, drones, services).

Shared fields: warranty_months_min (integer), quantity (integer),
max_total_hkd (number, HKD),
deadline (ISO date "YYYY-MM-DD", optional — leave null when the user has not
decided; it can be collected later when the supplier is contacted),
preferences (object).

Your job: return ONLY a JSON object with these keys:
- category: one of the category ids (including "other"), or null if not determined
- quantity: integer, or null
- attributes: object containing the attributes of the chosen category, or the
  item/keywords pair when category is "other"
- warranty_months_min: integer, or null
- preferences: object (may be empty)
- max_total_hkd: number (HKD, no currency symbol), or null
- deadline: ISO date string, or null
- out_of_scope: list of items the user mentioned that this system does NOT procure (e.g. rubber ducks, coffee, drones)
- clarification_needed: true if any required detail is still ambiguous or missing
- unclear_fields: list of field names that are ambiguous or missing
- answer: a short helpful reply, ONLY if the user asked a question about the form (e.g. "what do I put for category?") rather than providing request details; otherwise null

Rules:
- Never invent a value the user did not state; leave it null.
- If quantity or budget is missing, set clarification_needed=true and list them in unclear_fields. The deadline is optional: never ask for it, and leave it null when the user has not stated one. Never ask for a department; it is assigned from the requester's account automatically.
- Put any non-product item in out_of_scope and do NOT include it in the request.
- For products outside the typed categories, use category "other" with the
  item/keywords attributes instead of out_of_scope.
- If the user is asking a question instead of giving request details, set answer and do not mark clarification_needed.

Examples of the "other" distillation (item / keywords / search_query):
- "Laptops for my team of 5" -> item: "laptop"; keywords: []; search_query: "laptop"
- "I need noise cancelling headphones with bluetooth, under 200g" -> item: "noise cancelling headphones"; keywords: ["noise cancelling", "bluetooth", "under 200g"]; search_query: "noise cancelling bluetooth headphones"
- "Get me a Thinkpad with a 512gb SSD" -> item: "laptop"; keywords: ["Thinkpad", "512gb"]; search_query: "Thinkpad 512gb laptop"
"""


_REQUEST_USER_TEMPLATE = """\
Current draft (merge into this — keep already-confirmed fields unless the user changes them):
{draft_json}

User's latest message:
\"\"\"
{user_text}
\"\"\"
"""


def build_request_messages(
    user_text: str, current_draft: RequestDraft | None
) -> list[dict[str, str]]:
    """Chat-style request-parsing prompt: a system message (persona, catalog,
    contract, rules, few-shot examples) plus a user message (draft + text)."""
    draft_json = json.dumps(
        current_draft.model_dump() if current_draft else {}, ensure_ascii=False
    )
    system = _REQUEST_SYSTEM_TEMPLATE.format(categories=_category_summary())
    user = _REQUEST_USER_TEMPLATE.format(draft_json=draft_json, user_text=user_text)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_request_prompt(user_text: str, current_draft: RequestDraft | None) -> str:
    """Flat single-message variant of the request prompt (debugging aid)."""
    return "\n\n".join(m["content"] for m in build_request_messages(user_text, current_draft))


#: Placeholder prompt templates — populated during later phases.
PROPOSE_RESEARCH_STEPS = "prompts.propose_research_steps/{PROMPT_VERSION}"
EXPLAIN_RECOMMENDATION = "prompts.explain_recommendation/{PROMPT_VERSION}"