"""Print the contents of the offers and evidence tables.

Usage:
    uv run python scripts/inspect_db.py
"""

from __future__ import annotations

from procurement.repository import (
    Evidence,
    Offer,
    create_engine_from_url,
    init_db,
    session_factory,
)


def main() -> None:
    engine = create_engine_from_url()
    init_db(engine)
    Session = session_factory(engine)

    with Session() as session:
        offers = session.query(Offer).all()
        evidence = session.query(Evidence).order_by(Evidence.field).all()

        print("=== offers ===")
        if not offers:
            print("  (empty)")
        for o in offers:
            print(
                f"  {o.id}  supplier={o.supplier_id}  product={o.product_id}  "
                f"unit_price_minor={o.unit_price_minor}  qty={o.available_quantity}"
            )

        print("=== evidence ===")
        if not evidence:
            print("  (empty)")
        for e in evidence:
            print(
                f"  {e.field:16s} = {e.extracted_value!r:30s} "
                f"({e.offer_id}, {e.retrieved_at})"
            )


if __name__ == "__main__":
    main()
