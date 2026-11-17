"""Wizard intake parsing: draft → wizard state mapping and value coercion."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from procurement.catalog import OTHER_CATEGORY
from procurement.schemas import RequestDraft
from procurement.ui.wizard import (
    _draft_to_wizard_data,
    _widget_state_for,
)
from tests.conftest import MONITOR_ATTRIBUTES  # noqa: F401 - fixture module


def _draft(**overrides) -> RequestDraft:
    defaults = dict(
        category="monitor",
        quantity=3,
        attributes=dict(MONITOR_ATTRIBUTES),
        warranty_months_min=12,
        max_total_hkd="5000",
    )
    defaults.update(overrides)
    return RequestDraft(**defaults)


def test_parse_fills_typed_category_fields():
    data = _draft_to_wizard_data(_draft(), "monitors for my team", {})
    assert data["category_override"] == "monitor"
    assert data["quantity"] == 3
    assert data["budget_minor"] == 500_000
    assert data["warranty_months_min"] == 12
    assert data["attributes"]["resolution"] == "2560x1440"
    assert data["attributes"]["screen_inches_min"] == 27


def test_parse_keeps_current_values_when_unstated():
    current = {"quantity": 2, "budget_minor": 100_000}
    data = _draft_to_wizard_data(
        _draft(quantity=None, max_total_hkd=None, warranty_months_min=None),
        "monitors",
        current,
    )
    assert data["quantity"] == 2
    assert data["budget_minor"] == 100_000
    assert data["warranty_months_min"] is None


def test_parse_other_keeps_search_query():
    draft = _draft(
        category=OTHER_CATEGORY,
        attributes={
            "item": "laptop",
            "keywords": ["512gb"],
            "search_query": "512gb laptop",
        },
    )
    data = _draft_to_wizard_data(draft, "Laptops for my team of engineers", {})
    # The order record carries the distilled product name, not the sentence.
    assert data["attributes"]["item"] == "laptop"
    assert data["attributes"]["search_query"] == "512gb laptop"
    assert data["attributes"]["keywords"] == ["512gb"]
    assert data["category_override"] == OTHER_CATEGORY


def test_parse_coerces_string_int_and_bool_values():
    draft = _draft(category="keyboard", attributes={"wireless": "yes"})
    data = _draft_to_wizard_data(draft, "keyboards", {})
    assert data["attributes"]["wireless"] is True

    monitor_draft = _draft(attributes={"screen_inches_min": "27"})
    data = _draft_to_wizard_data(monitor_draft, "monitors", {})
    assert data["attributes"]["screen_inches_min"] == 27


def test_parse_drops_uncastable_values_as_no_preference():
    draft = _draft(attributes={"screen_inches_min": "big"})
    data = _draft_to_wizard_data(draft, "monitors", {})
    assert "screen_inches_min" not in data["attributes"]


def test_widget_state_coercions():
    assert _widget_state_for("screen_inches_min", "27", "monitor") == 27
    assert _widget_state_for("screen_inches_min", "big", "monitor") == 0
    assert _widget_state_for("wireless", "yes", "keyboard") == "Yes"
    assert _widget_state_for("wireless", None, "keyboard") == "No preference"
    assert _widget_state_for("resolution", "2560x1440", "monitor") == "2560x1440"
    assert _widget_state_for("resolution", "4k", "monitor") == "No preference"