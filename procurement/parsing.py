"""Deterministic conversion from a parsed ``RequestDraft`` to a ``PurchaseRequest``.

This is the boundary between the LLM (which fills the draft) and the rules
engine (which owns the validated request). Everything here is pure and unit
testable; the LLM is never trusted to do the conversion itself.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from .catalog import CATEGORIES
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
) -> PurchaseRequest:
    """Convert a confirmed draft into a validated request.

    Raises :class:`ClarificationNeeded` listing the fields that still need
    confirmation rather than guessing.
    """
    missing: list[str] = []

    if draft.clarification_needed:
        missing.extend(draft.unclear_fields)
    if draft.category is None or draft.category not in CATEGORIES:
        missing.append("category")
    if not draft.quantity or draft.quantity <= 0:
        missing.append("quantity")
    if not draft.department:
        missing.append("department")
    if draft.max_total_hkd is None:
        missing.append("max_total_hkd")
    if not draft.deadline:
        missing.append("deadline")
    if missing:
        raise ClarificationNeeded(missing)

    budget_minor = parse_hkd_minor(str(draft.max_total_hkd))
    if budget_minor is None:
        raise ClarificationNeeded(["max_total_hkd"], "could not parse the budget")

    deadline = _parse_deadline(draft.deadline)
    if deadline is None:
        raise ClarificationNeeded(["deadline"], "could not parse the deadline")

    # Enforce required attributes for the chosen category.
    spec = CATEGORIES[draft.category]
    attributes = dict(draft.attributes)
    missing_attrs = [
        f"attributes.{attr.name}"
        for attr in spec.attributes
        if attr.required and attributes.get(attr.name) in (None, "", [])
    ]
    if missing_attrs:
        raise ClarificationNeeded(missing_attrs)

    requirements = RequestRequirements(
        category=draft.category,
        attributes=attributes,
        warranty_months_min=draft.warranty_months_min,
    )

    return PurchaseRequest(
        request_id=request_id,
        requester=requester,
        department=draft.department,
        category=draft.category,
        quantity=draft.quantity,
        max_total_cost_minor=budget_minor,
        deadline=deadline,
        mandatory=requirements,
        preferences=draft.preferences,
        created_at=now or datetime.now(timezone.utc),
    )
