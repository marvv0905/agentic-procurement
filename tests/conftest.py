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


@pytest.fixture
def make_request():
    def _make(
        quantity: int = 6,
        max_total_minor: int = 1_200_000,
        requirements: RequestRequirements | None = None,
        **kwargs,
    ) -> PurchaseRequest:
        defaults = dict(
            request_id="REQ_001",
            requester="alice",
            department="engineering",
            category="monitor",
            quantity=quantity,
            max_total_cost_minor=max_total_minor,
            deadline=date(2026, 11, 20),
            created_at=_now(),
        )
        defaults.update(kwargs)
        req = PurchaseRequest(**defaults)
        if requirements is not None:
            req = req.model_copy(update={"mandatory": requirements})
        return req

    return _make


@pytest.fixture
def make_offer():
    def _make(
        offer_id: str = "OFF_001",
        supplier_id: str = "SUP_B",
        unit_price_minor: int = 180000,
        available_quantity: int = 8,
        shipping_minor: int = 15000,
        screen_inches: int = 27,
        resolution: str = "2560x1440",
        connectors: list[str] | None = None,
        warranty_months: int = 36,
        delivery_by: date | None = date(2026, 11, 18),
        **kwargs,
    ) -> OfferEvidence:
        defaults = dict(
            offer_id=offer_id,
            supplier_id=supplier_id,
            product_id="MON_014",
            unit_price_minor=unit_price_minor,
            available_quantity=available_quantity,
            shipping_minor=shipping_minor,
            screen_inches=screen_inches,
            resolution=resolution,
            connectors=connectors if connectors is not None else ["HDMI", "DisplayPort"],
            warranty_months=warranty_months,
            delivery_by=delivery_by,
            retrieved_at=_now(),
        )
        defaults.update(kwargs)
        return OfferEvidence(**defaults)

    return _make
