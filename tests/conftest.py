"""Shared test fixtures."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from procurement.schemas import (
    OfferEvidence,
    PurchaseRequest,
    RequestRequirements,
)


def _now() -> datetime:
    return datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


MONITOR_ATTRIBUTES = {
    "screen_inches_min": 27,
    "resolution": "2560x1440",
    "connectors": ["HDMI", "DisplayPort"],
}


@pytest.fixture
def make_request():
    def _make(
        quantity: int = 6,
        max_total_minor: int = 1_200_000,
        category: str = "monitor",
        attributes: dict | None = None,
        warranty_months_min: int | None = None,
        **kwargs,
    ) -> PurchaseRequest:
        if attributes is None:
            attributes = MONITOR_ATTRIBUTES if category == "monitor" else {}
        return PurchaseRequest(
            request_id="REQ_001",
            requester="alice",
            department="engineering",
            category=category,
            quantity=quantity,
            max_total_cost_minor=max_total_minor,
            deadline=date(2026, 11, 20),
            mandatory=RequestRequirements(
                category=category,
                attributes=attributes,
                warranty_months_min=warranty_months_min,
            ),
            created_at=_now(),
            **kwargs,
        )

    return _make


@pytest.fixture
def make_offer():
    def _make(
        offer_id: str = "OFF_001",
        supplier_id: str = "SUP_B",
        category: str = "monitor",
        unit_price_minor: int = 180000,
        available_quantity: int = 8,
        shipping_minor: int = 15000,
        attributes: dict | None = None,
        warranty_months: int = 36,
        delivery_by: date | None = date(2026, 11, 18),
        **kwargs,
    ) -> OfferEvidence:
        if attributes is None:
            attributes = MONITOR_ATTRIBUTES if category == "monitor" else {}
        defaults = dict(
            offer_id=offer_id,
            supplier_id=supplier_id,
            product_id="MON_014",
            category=category,
            unit_price_minor=unit_price_minor,
            available_quantity=available_quantity,
            shipping_minor=shipping_minor,
            attributes=attributes,
            warranty_months=warranty_months,
            delivery_by=delivery_by,
            retrieved_at=_now(),
        )
        defaults.update(kwargs)
        return OfferEvidence(**defaults)

    return _make
