"""Business rules (§5 of the project proposal).

Pure functions only — no I/O, no LLM, no database. Everything here is unit
testable. All money is integer HKD minor units.

Three-valued validation: each requirement returns PASS, FAIL, or UNKNOWN.
UNKNOWN means "not enough evidence to decide", never silently converted to a
pass or a fail.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from .money import verified_total
from .schemas import (
    ApprovalRole,
    ComparisonResult,
    EligibilityResult,
    FieldValidation,
    OfferEvidence,
    PurchaseRequest,
    ValidationStatus,
)

#: Number of comparable quotations required before the shortfall is waived (rule 7).
QUOTATION_TARGET = 3

#: Purchases at or above this total require a manager role (rule 6). HK$10,000.
MANAGER_THRESHOLD_MINOR = 10_000 * 100


def supplier_allowed(supplier_id: str, allowlist: Iterable[str]) -> bool:
    """Rule 1: only allowlisted suppliers may be recommended."""
    return supplier_id in set(allowlist)


def required_approval_role(total_minor: int) -> ApprovalRole:
    """Rule 6: total at/above threshold requires a manager, else a requester."""
    if total_minor >= MANAGER_THRESHOLD_MINOR:
        return ApprovalRole.MANAGER
    return ApprovalRole.REQUESTER


def resolved_total_minor(offer: OfferEvidence, quantity: int) -> int | None:
    """Return the complete verified total, or ``None`` if any term is unknown.

    Rule 4/5: a total with any unresolved charge must not be treated as valid.
    """
    if offer.unit_price_minor is None or offer.shipping_minor is None:
        return None
    return verified_total(offer.unit_price_minor, quantity, offer.shipping_minor)


def _check(
    field: str,
    status: ValidationStatus,
    reason: str,
    evidence_ids: Sequence[str] = (),
) -> FieldValidation:
    return FieldValidation(
        field=field, status=status, reason=reason, evidence_ids=list(evidence_ids)
    )


def validate_offer(
    request: PurchaseRequest,
    offer: OfferEvidence,
    budget_snapshot_minor: int | None = None,
) -> EligibilityResult:
    """Validate a single offer against the request and (optional) budget.

    Checks: mandatory specifications (rule 2), quantity sufficiency (rule 3),
    unknown mandatory information (rule 4), and budget ceilings (rule 5).
    """
    checks: list[FieldValidation] = []
    req = request.mandatory

    # Mandatory specification: screen size.
    if req.screen_inches_min is not None:
        if offer.screen_inches is None:
            checks.append(_check("screen_inches", ValidationStatus.UNKNOWN, "screen size not extracted"))
        elif offer.screen_inches >= req.screen_inches_min:
            checks.append(_check("screen_inches", ValidationStatus.PASS, "screen size meets minimum"))
        else:
            checks.append(_check("screen_inches", ValidationStatus.FAIL, "screen size below minimum"))

    # Mandatory specification: resolution.
    if req.resolution:
        if offer.resolution is None:
            checks.append(_check("resolution", ValidationStatus.UNKNOWN, "resolution not extracted"))
        elif offer.resolution == req.resolution:
            checks.append(_check("resolution", ValidationStatus.PASS, "resolution matches requirement"))
        else:
            checks.append(_check("resolution", ValidationStatus.FAIL, "resolution does not match requirement"))

    # Mandatory specification: connectors.
    if req.connectors:
        if offer.connectors is None:
            checks.append(_check("connectors", ValidationStatus.UNKNOWN, "connectors not extracted"))
        else:
            missing = [c for c in req.connectors if c not in offer.connectors]
            if missing:
                checks.append(_check("connectors", ValidationStatus.FAIL, f"missing connectors: {', '.join(missing)}"))
            else:
                checks.append(_check("connectors", ValidationStatus.PASS, "all required connectors present"))

    # Mandatory specification: warranty.
    if req.warranty_months_min is not None:
        if offer.warranty_months is None:
            checks.append(_check("warranty_months", ValidationStatus.UNKNOWN, "warranty not extracted"))
        elif offer.warranty_months >= req.warranty_months_min:
            checks.append(_check("warranty_months", ValidationStatus.PASS, "warranty meets minimum"))
        else:
            checks.append(_check("warranty_months", ValidationStatus.FAIL, "warranty below minimum"))

    # Quantity sufficiency (rule 3): a supplier must cover the entire quantity.
    if offer.available_quantity is None:
        checks.append(_check("available_quantity", ValidationStatus.UNKNOWN, "available quantity not extracted"))
    elif offer.available_quantity < request.quantity:
        checks.append(_check("available_quantity", ValidationStatus.FAIL, "insufficient stock for full quantity"))
    else:
        checks.append(_check("available_quantity", ValidationStatus.PASS, "sufficient stock for full quantity"))

    # Verified total and budget (rule 5).
    total = resolved_total_minor(offer, request.quantity)
    if total is None:
        checks.append(_check("verified_total", ValidationStatus.UNKNOWN, "unit price or shipping unresolved"))
    else:
        if total > request.max_total_cost_minor:
            checks.append(_check("verified_total", ValidationStatus.FAIL, "exceeds request budget"))
        elif budget_snapshot_minor is not None and total > budget_snapshot_minor:
            checks.append(_check("verified_total", ValidationStatus.FAIL, "exceeds department budget snapshot"))
        else:
            checks.append(_check("verified_total", ValidationStatus.PASS, "within budget"))

    # Aggregate status: any FAIL dominates; else any UNKNOWN; else PASS.
    statuses = {c.status for c in checks}
    if ValidationStatus.FAIL in statuses:
        overall = ValidationStatus.FAIL
    elif ValidationStatus.UNKNOWN in statuses:
        overall = ValidationStatus.UNKNOWN
    else:
        overall = ValidationStatus.PASS

    return EligibilityResult(
        offer_id=offer.offer_id,
        status=overall,
        checks=checks,
        verified_total_minor=total,
    )


def _rank_key(item: tuple[OfferEvidence, int]) -> tuple[int, object, str]:
    """Sort key: lowest total, then earlier delivery, then stable supplier id.

    ``date.min`` is used as a sentinel so offers missing a delivery date sort
    after any dated offer without crashing.
    """
    offer, total = item
    delivery = offer.delivery_by if offer.delivery_by is not None else date.min
    return (total, delivery, offer.supplier_id)


def compare_offers(
    request: PurchaseRequest,
    offers: Sequence[OfferEvidence],
    results: Sequence[EligibilityResult],
) -> ComparisonResult:
    """Rank fully eligible offers and select the lowest verified total (rule 8)."""
    eligible: list[tuple[OfferEvidence, int]] = []
    for offer, result in zip(offers, results):
        if result.status is ValidationStatus.PASS and result.verified_total_minor is not None:
            eligible.append((offer, result.verified_total_minor))

    ranked = sorted(eligible, key=_rank_key)
    selected = ranked[0][0].offer_id if ranked else None

    shortfall = len(ranked) < QUOTATION_TARGET

    return ComparisonResult(
        request_id=request.request_id,
        eligible_offers=[o.offer_id for o, _ in eligible],
        ranked_offers=[o.offer_id for o, _ in ranked],
        selected_offer_id=selected,
        quotation_shortfall=shortfall,
    )
