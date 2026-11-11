"""SQLAlchemy models and session setup.

Nine core tables mirror §8 of the proposal. Money columns are integer minor
units; timestamps are timezone aware. SQLite is the default backend.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    requester: Mapped[str] = mapped_column(String)
    department: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="monitor")
    quantity: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String, default="HKD")
    budget_minor: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[str] = mapped_column(String)
    requirements_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"))
    state: Mapped[str] = mapped_column(String, default="DRAFT")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_pages: Mapped[int] = mapped_column(Integer, default=30)
    max_actions: Mapped[int] = mapped_column(Integer, default=200)
    max_seconds: Mapped[int] = mapped_column(Integer, default=600)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    allowed_origin: Mapped[str] = mapped_column(String)
    approval_status: Mapped[str] = mapped_column(String, default="allowed")


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    product_id: Mapped[str] = mapped_column(String)
    unit_price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shipping_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warranty_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_by: Mapped[str | None] = mapped_column(String, nullable=True)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id"))
    url: Mapped[str] = mapped_column(String)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    field: Mapped[str] = mapped_column(String)
    extracted_value: Mapped[str | None] = mapped_column(String, nullable=True)
    source_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"))
    version: Mapped[int] = mapped_column(Integer)
    selected_offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id"))
    portal_payload_json: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String)
    budget_snapshot_minor: Mapped[int] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    proposal_hash: Mapped[str] = mapped_column(ForeignKey("proposals.hash"))
    reviewer: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    exceptions_approved_json: Mapped[str] = mapped_column(Text, default="[]")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    request_key: Mapped[str] = mapped_column(String, unique=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"))
    portal_record_id: Mapped[str | None] = mapped_column(String, nullable=True)
    verification_status: Mapped[str] = mapped_column(String, default="pending")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    result: Mapped[str] = mapped_column(String)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def default_database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///data/purchasepilot.db")


def create_engine_from_url(url: str | None = None):
    return create_engine(url or default_database_url())


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_and_seed(engine) -> None:
    """Create tables and seed suppliers (idempotent)."""
    init_db(engine)
    from .seed import generate

    data = generate()
    Session = session_factory(engine)
    with Session() as session:
        for sup in data.suppliers:
            if session.get(Supplier, sup.id) is None:
                session.add(
                    Supplier(
                        id=sup.id,
                        name=sup.name,
                        allowed_origin=sup.origin,
                        approval_status="allowed",
                    )
                )
        session.commit()


def _to_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def persist_extraction(
    session,
    *,
    supplier_id: str,
    product_id: str,
    url: str,
    fields: dict[str, object],
    retrieved_at: datetime,
) -> str:
    """Persist extracted fields as an ``Offer`` plus one ``Evidence`` row each.

    Best-effort maps known field names onto ``Offer`` columns: ``available_units``
    -> ``available_quantity`` and ``price_hkd`` -> ``unit_price_minor`` (via
    tolerant HKD parsing). Returns the offer id.
    """
    from .money import parse_hkd_minor

    offer_id = f"{supplier_id}:{product_id}"
    offer = session.get(Offer, offer_id)
    if offer is None:
        offer = Offer(id=offer_id, supplier_id=supplier_id, product_id=product_id)

    if "available_units" in fields:
        offer.available_quantity = _to_int(fields["available_units"])
    if "price_hkd" in fields:
        minor = parse_hkd_minor(fields["price_hkd"])
        if minor is not None:
            offer.unit_price_minor = minor

    session.add(offer)
    session.flush()

    for field, value in fields.items():
        session.add(
            Evidence(
                id=f"{offer_id}:{field}",
                offer_id=offer_id,
                url=url,
                retrieved_at=retrieved_at,
                field=field,
                extracted_value=str(value),
            )
        )
    session.commit()
    return offer_id
