"""Versioned prompts.

Prompts are versioned so evaluation runs are reproducible. The request-parsing
prompt is built dynamically from the category catalog so it stays in sync with
the attributes the rules engine actually validates.
"""

from __future__ import annotations

import json
from typing import Any

from ..catalog import CATEGORIES
from ..schemas import RequestDraft

PROMPT_VERSION = "v2"


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


_REQUEST_TEMPLATE = """\
You are the intake assistant for a company procurement system that sources office
equipment. You convert a conversation into a structured request.

Available product categories and their attributes:
{categories}

Shared fields (all categories): warranty_months_min (integer), quantity (integer),
max_total_hkd (number, HKD), deadline (ISO date "YYYY-MM-DD"), department (string),
preferences (object).

Your job: return ONLY a JSON object with these keys:
- category: one of the category ids, or null if not determined
- quantity: integer, or null
- attributes: object containing only the attributes of the chosen category
- warranty_months_min: integer, or null
- preferences: object (may be empty)
- max_total_hkd: number (HKD, no currency symbol), or null
- deadline: ISO date string, or null
- department: string, or null
- out_of_scope: list of items the user mentioned that this system does NOT procure (e.g. rubber ducks, coffee, drones)
- clarification_needed: true if any required detail is still ambiguous or missing
- unclear_fields: list of field names that are ambiguous or missing
- answer: a short helpful reply, ONLY if the user asked a question about the form (e.g. "what do I put for category?") rather than providing request details; otherwise null

Current draft (merge into this — keep already-confirmed fields unless the user changes them):
{draft_json}

Rules:
- Never invent a value the user did not state; leave it null.
- If quantity, budget, deadline, or department are missing, set clarification_needed=true and list them in unclear_fields.
- Put any non-procurement item in out_of_scope and do NOT include it in the request.
- If the user is asking a question instead of giving request details, set answer and do not mark clarification_needed.

User's latest message:
\"\"\"
{user_text}
\"\"\"
"""


def build_request_prompt(user_text: str, current_draft: RequestDraft | None) -> str:
    draft_json = json.dumps(
        current_draft.model_dump() if current_draft else {}, ensure_ascii=False
    )
    return _REQUEST_TEMPLATE.format(
        categories=_category_summary(),
        draft_json=draft_json,
        user_text=user_text,
    )


#: Placeholder prompt templates — populated during later phases.
PROPOSE_RESEARCH_STEPS = "prompts.propose_research_steps/{PROMPT_VERSION}"
EXPLAIN_RECOMMENDATION = "prompts.explain_recommendation/{PROMPT_VERSION}"
