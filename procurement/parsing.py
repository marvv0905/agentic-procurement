"""Deterministic conversion from a parsed ``RequestDraft`` to a ``PurchaseRequest``.

This is the boundary between the LLM (which fills the draft) and the rules
engine (which owns the validated request). Everything here is pure and unit
testable; the LLM is never trusted to do the conversion itself.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from .catalog import CATEGORIES, OTHER_CATEGORY
from .money import parse_hkd_minor
from .schemas import PurchaseRequest, RequestDraft, RequestRequirements


class ClarificationNeeded(Exception):
    """Raised when a draft cannot become a request without more information."""

    def __init__(self, missing_fields: list[str], message: str = "") -> None:
        self.missing_fields = list(dict.fromkeys(missing_fields))
        detail = message or "clarification needed: " + ", ".join(self.missing_fields)
        super().__init__(detail)


def _parse_deadline(value: str) -> date | None:
    if not value:
        return None
    value = value.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        from dateutil import parser

        return parser.parse(value).date()
    except Exception:
        return None


def draft_to_request(
    draft: RequestDraft,
    *,
    request_id: str,
    requester: str,
    now: datetime | None = None,
    requester_department: str = "",
) -> PurchaseRequest:
    """Convert a confirmed draft into a validated request.

    ``requester_department`` is the requester's account department — orders
    are auto-assigned from it, so a draft never needs to carry one (a stated
    department in the conversation still wins).

    Raises :class:`ClarificationNeeded` listing the fields that still need
    confirmation rather than guessing.
    """
    missing: list[str] = []

    if draft.clarification_needed:
        missing.extend(draft.unclear_fields)
    if draft.category is None or (
        draft.category not in CATEGORIES and draft.category != OTHER_CATEGORY
    ):
        missing.append("category")
    if draft.category == OTHER_CATEGORY:
        if not draft.attributes.get("item"):
            missing.append("item description")
        keywords = draft.attributes.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            missing.append("required keywords")
    if not draft.quantity or draft.quantity <= 0:
        missing.append("quantity")
    if draft.max_total_hkd is None:
        missing.append("max_total_hkd")
    if missing:
        raise ClarificationNeeded(missing)

    budget_minor = parse_hkd_minor(str(draft.max_total_hkd))
    if budget_minor is None:
        raise ClarificationNeeded(["max_total_hkd"], "could not parse the budget")

    # The delivery date is optional — it can be collected later when the
    # supplier is contacted. A stated-but-unparseable date still asks for
    # clarification rather than being silently dropped.
    deadline = _parse_deadline(draft.deadline) if draft.deadline else None
    if draft.deadline and deadline is None:
        raise ClarificationNeeded(["deadline"], "could not parse the deadline")

    # Specs are optional ("no preference"): only the attributes the requester
    # actually stated are carried into the request, and the rules engine only
    # enforces what is present. Empty values are dropped so the engine never
    # compares against "" or an empty list.
    if draft.category == OTHER_CATEGORY:
        attributes = {
            "item": str(draft.attributes.get("item", "")).strip(),
            "keywords": [str(k).strip() for k in (draft.attributes.get("keywords") or []) if str(k).strip()],
        }
        # The intake LLM distills a ready-to-type search query; research uses
        # it verbatim (wizard-created orders simply omit it and research
        # falls back to the deduplicated item+keywords join).
        search_query = str(draft.attributes.get("search_query") or "").strip()
        if search_query:
            attributes["search_query"] = search_query
    else:
        attributes = {
            key: value
            for key, value in draft.attributes.items()
            if value not in (None, "", [])
        }

    requirements = RequestRequirements(
        category=draft.category,
        attributes=attributes,
        warranty_months_min=draft.warranty_months_min,
    )

    return PurchaseRequest(
        request_id=request_id,
        requester=requester,
        department=(draft.department or "").strip() or requester_department,
        category=draft.category,
        quantity=draft.quantity,
        max_total_cost_minor=budget_minor,
        deadline=deadline,
        mandatory=requirements,
        preferences=draft.preferences,
        created_at=now or datetime.now(timezone.utc),
    )
