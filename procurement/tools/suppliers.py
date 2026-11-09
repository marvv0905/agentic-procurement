"""Supplier research tool stubs (§9).

These are application-level interfaces, not claimed Stagehand SDK signatures.
The research phase may browse suppliers; it can never access portal submission.
"""

from __future__ import annotations

from ..schemas import ComparisonResult, EligibilityResult, OfferEvidence, PurchaseRequest


def research_supplier(request_id: str, supplier_id: str) -> list[OfferEvidence]:
    """Browse a supplier site and extract product evidence."""
    raise NotImplementedError(
        f"research_supplier({request_id!r}, {supplier_id!r}): Stagehand not wired yet."
    )


def validate_offer(request: PurchaseRequest, offer: OfferEvidence) -> EligibilityResult:
    """Delegate to the deterministic rules engine (already implemented)."""
    from ..rules import validate_offer as _validate

    return _validate(request, offer)


def compare_offers(
    request: PurchaseRequest,
    offers: list[OfferEvidence],
    results: list[EligibilityResult],
) -> ComparisonResult:
    """Delegate to the deterministic rules engine (already implemented)."""
    from ..rules import compare_offers as _compare

    return _compare(request, offers, results)
