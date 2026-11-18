"""Per-offer eligibility verdicts rebuilt from the persisted human record.

The rules engine runs inside the blackbox and only its outputs are stored.
For the evidence panel we re-run the *deterministic* validation against the
persisted ``products`` rows — same rules, same data, no LLM — so the UI can
show PASS / UNKNOWN / FAIL stamps (and why) for every offer the agent read,
even after a process restart when the live event feed is gone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .catalog import CATEGORIES
from .repository import Product, Supplier, User
from .research import _order_to_request
from .rules import validate_offer
from .schemas import OfferEvidence, ValidationStatus


@dataclass(frozen=True)
class OfferVerdict:
    """Everything the evidence panel renders for one offer."""

    product_id: int
    supplier_id: int
    supplier_name: str
    model_name: str
    status: str  # "PASS" | "UNKNOWN" | "FAIL"
    issues: list[str]  # human-readable FAIL/UNKNOWN reasons, empty when PASS
    total_minor: int | None
    recommended: bool
    #: Raw row for meta lines (stock, delivery, warranty).
    product: Product


#: Friendly labels for non-attribute check fields from the rules engine.
_FIELD_LABELS = {
    "warranty_months": "Warranty",
    "available_quantity": "Stock",
    "verified_total": "Total",
    "category": "Category",
    "item keywords": "Item keywords",
    "item_type": "Item type",
}


def _label_for(field: str, category: str) -> str:
    spec = CATEGORIES.get(category)
    if spec is not None:
        attr = spec.attribute_map.get(field)
        if attr is not None:
            return attr.display_name
    return _FIELD_LABELS.get(field, field.replace("_", " "))


def _issues_for(result, category: str) -> list[str]:
    """Human lines for a non-PASS verdict: every FAIL, then every UNKNOWN."""
    fails, unknowns = [], []
    for check in result.checks:
        if check.status is ValidationStatus.FAIL:
            fails.append(f"{_label_for(check.field, category)}: {check.reason}")
        elif check.status is ValidationStatus.UNKNOWN:
            unknowns.append(f"{_label_for(check.field, category)}: {check.reason}")
    return fails + unknowns


def _to_evidence(product: Product) -> OfferEvidence:
    """Rebuild typed evidence from a stored product row (unknowns stay None)."""
    delivery_by = None
    if product.delivery_days is not None and product.last_seen_at is not None:
        delivery_by = product.last_seen_at.date() + timedelta(
            days=product.delivery_days
        )
    return OfferEvidence(
        offer_id=f"{product.supplier_id}:{product.product_key}",
        supplier_id=str(product.supplier_id),
        product_id=product.product_key,
        category=product.category,
        model_name=product.model_name,
        unit_price_minor=product.unit_price_minor,
        available_quantity=product.available_quantity,
        shipping_minor=product.shipping_minor,
        attributes=json.loads(product.attributes_json or "{}"),
        warranty_months=product.warranty_months,
        delivery_by=delivery_by,
        retrieved_at=product.last_seen_at,
    )


def order_verdicts(session: Session, order) -> list[OfferVerdict]:
    """Verdicts for every offer this order's run saw, ranked for display.

    ``PASS`` first (lowest verified total leads), then ``UNKNOWN``, then
    ``FAIL``. The recommended offer always sorts to the top of its group.
    """
    requester = session.get(User, order.requester_id)
    if requester is None:  # pragma: no cover - defensive
        return []
    request = _order_to_request(order, requester)

    rows = list(
        session.execute(
            select(Product, Supplier)
            .join(Supplier, Product.supplier_id == Supplier.id)
            .where(
                Supplier.status == "approved",
                Product.last_seen_at >= order.created_at,
            )
        )
    )

    verdicts: list[OfferVerdict] = []
    for product, supplier in rows:
        result = validate_offer(request, _to_evidence(product))
        verdicts.append(
            OfferVerdict(
                product_id=product.id,
                supplier_id=supplier.id,
                supplier_name=supplier.name,
                model_name=product.model_name or product.product_key,
                status=result.status.value,
                issues=_issues_for(result, request.mandatory.category),
                total_minor=result.verified_total_minor,
                recommended=(order.selected_product_id == product.id),
                product=product,
            )
        )

    rank = {"PASS": 0, "UNKNOWN": 1, "FAIL": 2}

    def sort_key(v: OfferVerdict):
        return (
            rank.get(v.status, 3),
            0 if v.recommended else 1,
            v.total_minor if v.total_minor is not None else 10**15,
        )

    verdicts.sort(key=sort_key)
    return verdicts


def counts(verdicts: list[OfferVerdict]) -> dict[str, int]:
    """PASS / UNKNOWN / FAIL counts for the evidence panel's filter tabs."""
    out = {"PASS": 0, "UNKNOWN": 0, "FAIL": 0}
    for v in verdicts:
        if v.status in out:
            out[v.status] += 1
    return out