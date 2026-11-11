from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from procurement.repository import (
    Evidence,
    Offer,
    init_db,
    persist_extraction,
    session_factory,
)


def _memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_persist_extraction_writes_offer_and_evidence():
    engine = _memory_engine()
    init_db(engine)
    Session = session_factory(engine)

    with Session() as session:
        offer_id = persist_extraction(
            session,
            supplier_id="SUP_A",
            product_id="MON_1",
            url="http://127.0.0.1:8100/supplier/SUP_A/catalog",
            fields={
                "model_name": "Vision 24",
                "screen_inches": 24,
                "price_hkd": "HK$3,300.33",
                "available_units": 5,
            },
            retrieved_at=datetime.now(timezone.utc),
        )

        assert offer_id == "SUP_A:MON_1"

        offer = session.get(Offer, offer_id)
        assert offer.unit_price_minor == 330033
        assert offer.available_quantity == 5

        evidence = session.query(Evidence).filter_by(offer_id=offer_id).all()
        assert {e.field for e in evidence} == {
            "model_name",
            "screen_inches",
            "price_hkd",
            "available_units",
        }
