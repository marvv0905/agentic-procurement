import pytest
from pydantic import ValidationError

from procurement.schemas import (
    EligibilityResult,
    OfferEvidence,
    PurchaseRequest,
    ValidationStatus,
)


def test_quantity_must_be_positive(make_request):
    with pytest.raises(ValidationError):
        make_request(quantity=0)


def test_absent_facts_default_none(make_offer):
    offer = make_offer(unit_price_minor=None, available_quantity=None, warranty_months=None)
    assert offer.unit_price_minor is None
    assert offer.available_quantity is None
    assert offer.warranty_months is None
    assert offer.connectors == ["HDMI", "DisplayPort"]


def test_eligibility_pass_property():
    result = EligibilityResult(offer_id="OFF_1", status=ValidationStatus.PASS)
    assert result.eligible


def test_offer_rejects_unknown_fields(make_offer):
    with pytest.raises(ValidationError):
        make_offer(nonexistent_field=1)
