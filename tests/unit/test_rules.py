from procurement.rules import (
    MANAGER_THRESHOLD_MINOR,
    QUOTATION_TARGET,
    compare_offers,
    required_approval_role,
    resolved_total_minor,
    supplier_allowed,
    validate_offer,
)
from procurement.schemas import ApprovalRole, ValidationStatus


def test_supplier_allowlist():
    assert supplier_allowed("SUP_B", ["SUP_A", "SUP_B"])
    assert not supplier_allowed("SUP_X", ["SUP_A", "SUP_B"])


def test_approval_threshold():
    assert required_approval_role(MANAGER_THRESHOLD_MINOR - 1) is ApprovalRole.REQUESTER
    assert required_approval_role(MANAGER_THRESHOLD_MINOR) is ApprovalRole.MANAGER


def test_fully_eligible_monitor(make_request, make_offer):
    req = make_request()
    offer = make_offer()
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.PASS
    assert result.eligible
    assert result.verified_total_minor == 1_095_000


def test_low_stock_fails_quantity(make_request, make_offer):
    req = make_request(quantity=6)
    offer = make_offer(available_quantity=2)
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.FAIL
    assert any(
        c.field == "available_quantity" and c.status is ValidationStatus.FAIL
        for c in result.checks
    )


def test_warranty_failure(make_request, make_offer):
    req = make_request(warranty_months_min=36)
    offer = make_offer(warranty_months=12)
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.FAIL
    assert any(
        c.field == "warranty_months" and c.status is ValidationStatus.FAIL
        for c in result.checks
    )


def test_unknown_price_yields_unknown_total(make_request, make_offer):
    req = make_request()
    offer = make_offer(unit_price_minor=None)
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.UNKNOWN
    assert result.verified_total_minor is None


def test_over_budget_fails(make_request, make_offer):
    req = make_request(max_total_minor=100000)  # HK$1,000 < HK$10,950 total
    offer = make_offer()
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.FAIL
    assert any(
        c.field == "verified_total" and c.status is ValidationStatus.FAIL
        for c in result.checks
    )


def test_missing_attribute_is_unknown(make_request, make_offer):
    req = make_request(attributes={"screen_inches_min": 27, "resolution": "2560x1440", "connectors": ["HDMI"]})
    offer = make_offer(attributes={"screen_inches_min": None, "resolution": "2560x1440", "connectors": ["HDMI"]})
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.UNKNOWN
    assert any(c.field == "screen_inches_min" and c.status is ValidationStatus.UNKNOWN for c in result.checks)


def test_resolution_mismatch_fails(make_request, make_offer):
    req = make_request(attributes={"resolution": "2560x1440", "screen_inches_min": 27, "connectors": ["HDMI"]})
    offer = make_offer(attributes={"resolution": "1920x1080", "screen_inches_min": 27, "connectors": ["HDMI"]})
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.FAIL
    assert any(c.field == "resolution" and c.status is ValidationStatus.FAIL for c in result.checks)


def test_keyboard_eligible(make_request, make_offer):
    attrs = {"layout": "tenkeyless", "switch_type": "mechanical", "wireless": True}
    req = make_request(category="keyboard", attributes=attrs)
    offer = make_offer(category="keyboard", attributes=attrs)
    assert validate_offer(req, offer).status is ValidationStatus.PASS


def test_keyboard_wireless_mismatch_fails(make_request, make_offer):
    req = make_request(category="keyboard", attributes={"layout": "tenkeyless", "switch_type": "mechanical", "wireless": True})
    offer = make_offer(category="keyboard", attributes={"layout": "tenkeyless", "switch_type": "mechanical", "wireless": False})
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.FAIL
    assert any(c.field == "wireless" and c.status is ValidationStatus.FAIL for c in result.checks)


def test_chair_weight_below_minimum_fails(make_request, make_offer):
    req = make_request(category="chair", attributes={"material": "mesh", "armrests": True, "max_weight_kg": 120})
    offer = make_offer(category="chair", attributes={"material": "mesh", "armrests": True, "max_weight_kg": 100})
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.FAIL
    assert any(c.field == "max_weight_kg" and c.status is ValidationStatus.FAIL for c in result.checks)


def test_category_mismatch_fails(make_request, make_offer):
    req = make_request(category="monitor")
    offer = make_offer(category="keyboard")
    result = validate_offer(req, offer)
    assert result.status is ValidationStatus.FAIL
    assert any(c.field == "category" and c.status is ValidationStatus.FAIL for c in result.checks)


def test_resolved_total_requires_price_and_shipping(make_offer):
    assert resolved_total_minor(make_offer(unit_price_minor=None), 6) is None
    assert resolved_total_minor(make_offer(shipping_minor=None), 6) is None
    assert resolved_total_minor(make_offer(), 6) == 1_095_000


def test_compare_offers_selects_lowest_total(make_request, make_offer):
    req = make_request()
    offers = [
        make_offer(offer_id="OFF_1", supplier_id="SUP_A", unit_price_minor=200000, shipping_minor=0),
        make_offer(offer_id="OFF_2", supplier_id="SUP_B", unit_price_minor=180000, shipping_minor=15000),
    ]
    results = [validate_offer(req, o) for o in offers]
    comparison = compare_offers(req, offers, results)
    # OFF_1 total 1,200,000 > OFF_2 total 1,095,000.
    assert comparison.selected_offer_id == "OFF_2"


def test_compare_offers_shortfall_detected(make_request, make_offer):
    req = make_request()
    offers = [make_offer(offer_id="OFF_1")]
    results = [validate_offer(req, o) for o in offers]
    comparison = compare_offers(req, offers, results)
    assert comparison.quotation_shortfall
    assert len(comparison.ranked_offers) < QUOTATION_TARGET
