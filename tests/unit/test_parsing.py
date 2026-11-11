from datetime import datetime, timezone

import pytest

from procurement.parsing import ClarificationNeeded, draft_to_request
from procurement.schemas import RequestDraft

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)

MONITOR_ATTRS = {
    "screen_inches_min": 27,
    "resolution": "2560x1440",
    "connectors": ["HDMI", "DisplayPort"],
}


def _draft(**kwargs) -> RequestDraft:
    base = dict(
        category="monitor",
        quantity=6,
        attributes=MONITOR_ATTRS,
        warranty_months_min=36,
        max_total_hkd=8000,
        deadline="2026-11-20",
        department="engineering",
    )
    base.update(kwargs)
    return RequestDraft(**base)


def test_converts_number_budget_to_minor():
    req = draft_to_request(_draft(max_total_hkd=8000), request_id="R1", requester="alice", now=NOW)
    assert req.max_total_cost_minor == 800000
    assert req.quantity == 6
    assert req.department == "engineering"
    assert req.mandatory.category == "monitor"
    assert req.mandatory.attributes["screen_inches_min"] == 27


def test_parses_currency_string_budget():
    req = draft_to_request(
        _draft(max_total_hkd="HK$8,000"), request_id="R1", requester="alice", now=NOW
    )
    assert req.max_total_cost_minor == 800000


def test_missing_quantity_raises():
    with pytest.raises(ClarificationNeeded) as exc:
        draft_to_request(_draft(quantity=None), request_id="R1", requester="alice", now=NOW)
    assert "quantity" in exc.value.missing_fields


def test_clarification_flag_propagates():
    draft = _draft(clarification_needed=True, unclear_fields=["resolution"])
    with pytest.raises(ClarificationNeeded) as exc:
        draft_to_request(draft, request_id="R1", requester="alice", now=NOW)
    assert "resolution" in exc.value.missing_fields


def test_bad_deadline_raises():
    with pytest.raises(ClarificationNeeded) as exc:
        draft_to_request(
            _draft(deadline="not-a-real-date"), request_id="R1", requester="alice", now=NOW
        )
    assert "deadline" in exc.value.missing_fields


def test_bad_budget_raises():
    with pytest.raises(ClarificationNeeded) as exc:
        draft_to_request(
            _draft(max_total_hkd="lots"), request_id="R1", requester="alice", now=NOW
        )
    assert "max_total_hkd" in exc.value.missing_fields


def test_missing_category_raises():
    with pytest.raises(ClarificationNeeded) as exc:
        draft_to_request(_draft(category=None), request_id="R1", requester="alice", now=NOW)
    assert "category" in exc.value.missing_fields


def test_unknown_category_raises():
    with pytest.raises(ClarificationNeeded) as exc:
        draft_to_request(_draft(category="drone"), request_id="R1", requester="alice", now=NOW)
    assert "category" in exc.value.missing_fields


def test_missing_required_attribute_raises():
    draft = _draft(attributes={"screen_inches_min": 27})
    with pytest.raises(ClarificationNeeded) as exc:
        draft_to_request(draft, request_id="R1", requester="alice", now=NOW)
    assert "attributes.resolution" in exc.value.missing_fields
    assert "attributes.connectors" in exc.value.missing_fields


def test_keyboard_draft_converts():
    draft = _draft(
        category="keyboard",
        attributes={"layout": "tenkeyless", "switch_type": "mechanical", "wireless": True},
    )
    req = draft_to_request(draft, request_id="R1", requester="alice", now=NOW)
    assert req.category == "keyboard"
    assert req.mandatory.attributes["layout"] == "tenkeyless"


def test_out_of_scope_is_ignored_by_converter():
    draft = _draft(out_of_scope=["rubber ducks", "coffee beans"])
    req = draft_to_request(draft, request_id="R1", requester="alice", now=NOW)
    assert req.category == "monitor"
