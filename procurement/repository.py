"""SQLAlchemy models and session setup — human-record schema.

Seven tables store everything the UI needs: users, suppliers, products
(scraped by the agent), orders, human decisions, and chat persistence.

The agent's internals (runs, audit events, evidence, proposals) are
deliberately NOT persisted — the database is the human record, the agent is a
blackbox. Money columns are integer minor units (HKD cents); timestamps are
timezone aware.
"""

from __future__ import annotations

import os
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="employee")
    #: The member's department — orders are auto-assigned from it.
    department: Mapped[str | None] = mapped_column(String, nullable=True)
    password_hash: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    origin: Mapped[str] = mapped_column(String)
    #: Aggregator suppliers: catalog/search entry URL, optionally containing a
    #: "{query}" placeholder. When set, research skips the homepage and goes
    #: straight here (and scrapes product pages deeply). Null for plain
    #: catalog sites (mock world).
    catalog_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="approved")
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("supplier_id", "product_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    product_key: Mapped[str] = mapped_column(String)
    model_name: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String)
    attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    #: Nullable on purpose — "unknown" is meaningful (three-valued logic).
    unit_price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shipping_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warranty_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String, unique=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    department: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    budget_minor: Mapped[int] = mapped_column(Integer)
    #: Needed-by date — optional, collected later when the supplier is contacted.
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    requirements_json: Mapped[str] = mapped_column(Text, default="{}")
    preferences_json: Mapped[str] = mapped_column(Text, default="{}")
    selected_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="DRAFT")
    source: Mapped[str] = mapped_column(String, default="wizard")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    placed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OrderDecision(Base):
    __tablename__ = "order_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    decision: Mapped[str] = mapped_column(String)  # approved | rejected | cancelled
    decided_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String)  # snapshot of decider's role
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String, default="New chat")
    draft_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str] = mapped_column(String)  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def default_database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///data/purchasepilot.db")


def create_engine_from_url(url: str | None = None):
    """Create the engine. SQLite needs cross-thread access for background research."""
    url = url or default_database_url()
    engine = create_engine(url, connect_args={"check_same_thread": False})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_connection, connection_record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def init_db(engine) -> None:
    Base.metadata.create_all(engine)


def reset_db(engine) -> None:
    """Drop everything and recreate. Demo data is disposable.

    Drops *all* tables actually present in the DB — including leftovers from
    a previous schema — so stale databases (e.g. the old agent-centric tables,
    where ``offers`` referenced ``suppliers``) reset cleanly. FK enforcement is
    disabled for the dropping connection; note SQLite silently ignores the
    pragma inside a transaction, hence the AUTOCOMMIT connection.
    """
    from sqlalchemy import inspect

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in inspect(conn).get_table_names():
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table}"')
    Base.metadata.create_all(engine)


def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _is_stale_schema(engine) -> bool:
    """True when the DB still carries the old agent-centric tables."""
    from sqlalchemy import inspect

    tables = set(inspect(engine).get_table_names())
    return bool(tables & {"requests", "runs", "offers", "evidence", "proposals"})


def _rebuild_orders_nullable_deadline(engine, conn) -> None:
    """Make orders.deadline nullable (SQLite cannot ALTER a NOT NULL).

    The needed-by date became optional (collected later from the supplier),
    but existing databases carry ``deadline DATE NOT NULL``. Rebuild the table
    with the model's current schema: create a copy, transfer the rows, swap.
    Runs with foreign keys disabled on this connection (the same listener
    turns them on for every new connection, so disable explicitly).
    """
    from sqlalchemy.schema import CreateTable

    table = Base.metadata.tables["orders"]
    ddl = str(CreateTable(table).compile(dialect=engine.dialect))
    columns = ", ".join(f'"{c.name}"' for c in table.columns)

    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    conn.exec_driver_sql("DROP TABLE IF EXISTS orders_new")
    # The temporary table's self-declaring DDL references the final table
    # names — harmless while foreign keys are off.
    conn.exec_driver_sql(ddl.replace("CREATE TABLE orders", "CREATE TABLE orders_new", 1))
    conn.exec_driver_sql(f"INSERT INTO orders_new ({columns}) SELECT {columns} FROM orders")
    conn.exec_driver_sql("DROP TABLE orders")
    conn.exec_driver_sql("ALTER TABLE orders_new RENAME TO orders")


def _ensure_columns(engine) -> None:
    """Lightweight auto-migration: add missing columns to existing tables.

    SQLite ``create_all`` never alters existing tables, so a DB created before
    a schema change would crash at runtime. New columns are registered with
    ALTER statements; constraint loosening goes through the rebuild helper.
    """
    from sqlalchemy import inspect, text

    migrations: dict[str, list[str]] = {
        "suppliers": ["ALTER TABLE suppliers ADD COLUMN catalog_url TEXT"],
        "users": ["ALTER TABLE users ADD COLUMN department TEXT"],
    }
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    if not existing:
        return  # fresh DB — create_all builds the current schema anyway

    # orders.deadline: NOT NULL -> nullable (needed-by is now optional).
    orders_needs_rebuild = False
    if "orders" in existing:
        deadline_col = next(
            (c for c in inspector.get_columns("orders") if c["name"] == "deadline"),
            None,
        )
        orders_needs_rebuild = deadline_col is not None and not deadline_col.get(
            "nullable", True
        )

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for table, statements in migrations.items():
            if table not in existing:
                continue
            columns = {c["name"] for c in inspector.get_columns(table)}
            for statement in statements:
                column = statement.split("ADD COLUMN ", 1)[1].split()[0]
                if column not in columns:
                    conn.execute(text(statement))
        if orders_needs_rebuild:
            _rebuild_orders_nullable_deadline(engine, conn)


def init_and_seed(engine) -> None:
    """Create tables and seed core data (admin user + mock suppliers).

    If an old-schema database is present, it is dropped and recreated — demo
    data is disposable by design.
    """
    if _is_stale_schema(engine):
        reset_db(engine)
    else:
        init_db(engine)
        _ensure_columns(engine)

    from .seed import seed_core

    Session = session_factory(engine)
    with Session() as session:
        seed_core(session)


def upsert_product(
    session,
    *,
    supplier_id: int,
    product_key: str,
    category: str,
    model_name: str | None = None,
    attributes: dict | None = None,
    unit_price_minor: int | None = None,
    shipping_minor: int | None = None,
    available_quantity: int | None = None,
    warranty_months: int | None = None,
    delivery_days: int | None = None,
    retrieved_at: datetime | None = None,
) -> Product:
    """Insert or refresh a product row keyed by (supplier_id, product_key).

    Unknown facts stay ``None`` — never zero or invented defaults. Existing
    known values are only overwritten when a fresh value is also known.
    """
    import json

    retrieved_at = retrieved_at or datetime.now(timezone.utc)

    product = (
        session.query(Product)
        .filter_by(supplier_id=supplier_id, product_key=product_key)
        .one_or_none()
    )
    if product is None:
        product = Product(
            supplier_id=supplier_id, product_key=product_key, category=category
        )
        session.add(product)

    if model_name:
        product.model_name = model_name
    if attributes is not None:
        product.attributes_json = json.dumps(attributes, ensure_ascii=False)
    if unit_price_minor is not None:
        product.unit_price_minor = unit_price_minor
    if shipping_minor is not None:
        product.shipping_minor = shipping_minor
    if available_quantity is not None:
        product.available_quantity = available_quantity
    if warranty_months is not None:
        product.warranty_months = warranty_months
    if delivery_days is not None:
        product.delivery_days = delivery_days
    product.last_seen_at = retrieved_at

    session.commit()
    session.refresh(product)
    return product