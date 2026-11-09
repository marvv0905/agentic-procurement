"""Approval binding and material-change detection.

An approval is bound to an exact, immutable proposal hash (rule 10 in the
proposal: "Approval is bound to exact immutable proposal payload"). Any change
to a material fact — supplier, product, quantity, price, charges, warranty,
delivery, policy, or budget snapshot — invalidates the approval (rule 9).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .schemas import ApprovalRecord, Proposal

#: Fields whose change invalidates an approval (rule 9).
MATERIAL_FIELDS = (
    "supplier_id",
    "product_id",
    "quantity",
    "unit_price_minor",
    "shipping_minor",
    "warranty_months",
    "delivery_by",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def compute_proposal_hash(
    selected_offer_id: str,
    portal_payload: dict[str, Any],
    policy_version: str,
    budget_snapshot_minor: int,
) -> str:
    """Deterministic SHA-256 hash of the immutable proposal contents."""
    canonical = _canonical(
        {
            "selected_offer_id": selected_offer_id,
            "portal_payload": portal_payload,
            "policy_version": policy_version,
            "budget_snapshot_minor": budget_snapshot_minor,
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approval_matches_proposal(approval: ApprovalRecord, proposal: Proposal) -> bool:
    """True when the approval was issued for this exact proposal version."""
    return approval.proposal_hash == proposal.hash


def material_change_between(
    before: dict[str, Any],
    after: dict[str, Any],
    policy_before: str,
    policy_after: str,
    budget_before: int,
    budget_after: int,
) -> bool:
    """Return True if any material fact, policy, or budget changed."""
    if policy_before != policy_after or budget_before != budget_after:
        return True
    for field in MATERIAL_FIELDS:
        if before.get(field) != after.get(field):
            return True
    return False
