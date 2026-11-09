"""Populate the SQLite database with seeded synthetic data for a demo.

Usage: ``uv run python scripts/seed_demo.py``
"""

from __future__ import annotations

from procurement import seed as seed_module
from procurement.repository import (
    Supplier,
    create_engine_from_url,
    init_db,
    session_factory,
)


def main() -> None:
    engine = create_engine_from_url()
    init_db(engine)
    Session = session_factory(engine)

    data = seed_module.generate()

    with Session() as session:
        session.query(Supplier).delete()
        for sup in data.suppliers:
            session.add(
                Supplier(
                    id=sup.id,
                    name=sup.name,
                    allowed_origin=sup.origin,
                    approval_status="allowed",
                )
            )
        session.commit()

    print(f"Seeded {len(data.suppliers)} suppliers and {len(data.listings)} listings "
          f"(seed={data.seed})")


if __name__ == "__main__":
    main()
