from datetime import datetime, timezone

from procurement.approvals import (
    compute_proposal_hash,
    material_change_between,
)
from procurement.schemas import ApprovalRecord, Proposal

_NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


def test_hash_is_deterministic():
    payload = {"supplier_id": "SUP_B", "quantity": 6}
    h1 = compute_proposal_hash("OFF_1", payload, "policy_v1", 5000000)
    h2 = compute_proposal_hash("OFF_1", payload, "policy_v1", 5000000)
    assert h1 == h2


def test_hash_changes_with_payload():
    base = compute_proposal_hash("OFF_1", {"quantity": 6}, "policy_v1", 5000000)
    changed = compute_proposal_hash("OFF_1", {"quantity": 7}, "policy_v1", 5000000)
    assert base != changed


def test_approval_matches_proposal_hash():
    from procurement.approvals import approval_matches_proposal

    h = compute_proposal_hash("OFF_1", {"quantity": 6}, "policy_v1", 5000000)
    approval = ApprovalRecord(
        proposal_id="P1",
        proposal_hash=h,
        reviewer="bob",
        role="manager",
        decision="approved",
        decided_at=_NOW,
    )
    proposal = Proposal(
        proposal_id="P1",
        request_id="REQ_1",
        version=1,
        selected_offer_id="OFF_1",
        portal_payload={"quantity": 6},
        policy_version="policy_v1",
        budget_snapshot_minor=5000000,
        created_at=_NOW,
        hash=h,
    )
    assert approval_matches_proposal(approval, proposal)


def test_material_change_detects_fields():
    before = {"supplier_id": "SUP_B", "quantity": 6}
    after = {"supplier_id": "SUP_B", "quantity": 7}
    assert material_change_between(before, after, "policy_v1", "policy_v1", 100, 100)


def test_material_change_detects_policy_and_budget():
    before = {"supplier_id": "SUP_B", "quantity": 6}
    after = {"supplier_id": "SUP_B", "quantity": 6}
    assert material_change_between(before, after, "policy_v1", "policy_v2", 100, 100)
    assert material_change_between(before, after, "policy_v1", "policy_v1", 100, 200)
    assert not material_change_between(before, after, "policy_v1", "policy_v1", 100, 100)
