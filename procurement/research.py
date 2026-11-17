"""Research orchestrator — the blackbox boundary.

Given a submitted order, this module drives the agent: for every approved
supplier, browse and extract products, persist them as ``products`` rows, then
run the deterministic rules engine to select the best compliant offer. The
order ends up ``PENDING_APPROVAL`` (with a selected product) or ``NO_OFFER``.

Nothing about the research process is *stored* — only its outputs (products,
selected product, order status). The live "what the agent is doing" feed is
emitted to the ephemeral :mod:`procurement.events` store instead: durable
outputs for the human record, transient progress for the run console.
Designed to run inside a background thread via :func:`start_research_thread`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import select

from . import events, order_service
from .money import format_hkd
from .repository import Product, Supplier, User, session_factory, upsert_product
from .schemas import OfferEvidence, PurchaseRequest, RequestRequirements
from .tools.suppliers import compare_offers, research_supplier, validate_offer

logger = logging.getLogger(__name__)


def _order_to_request(order, requester: User) -> PurchaseRequest:
    """Rebuild the typed request the rules engine expects from an order row."""
    requirements = json.loads(order.requirements_json or "{}")
    return PurchaseRequest(
        request_id=order.order_no,
        requester=requester.display_name,
        department=order.department,
        category=order.category,
        quantity=order.quantity,
        max_total_cost_minor=order.budget_minor,
        deadline=order.deadline,
        mandatory=RequestRequirements(
            category=requirements.get("category", order.category),
            attributes=requirements.get("attributes", {}),
            warranty_months_min=requirements.get("warranty_months_min"),
        ),
        preferences=json.loads(order.preferences_json or "{}"),
        created_at=order.created_at,
    )


def _evidence_delivery_days(evidence: OfferEvidence) -> int | None:
    if evidence.delivery_by is None:
        return None
    return (evidence.delivery_by - datetime.now(timezone.utc).date()).days


def _persist_evidence(session, supplier: Supplier, evidence: OfferEvidence) -> None:
    upsert_product(
        session,
        supplier_id=supplier.id,
        product_key=evidence.product_id,
        category=evidence.category,
        model_name=evidence.model_name,
        attributes=evidence.attributes,
        unit_price_minor=evidence.unit_price_minor,
        shipping_minor=evidence.shipping_minor,
        available_quantity=evidence.available_quantity,
        warranty_months=evidence.warranty_months,
        delivery_days=_evidence_delivery_days(evidence),
        retrieved_at=evidence.retrieved_at,
    )


async def _research(engine, order_id: int) -> None:
    Session = session_factory(engine)
    with Session() as session:
        order = order_service.get_order(session, order_id)
        if order is None or order.status != order_service.OrderStatus.RESEARCHING.value:
            return

        requester = session.get(User, order.requester_id)
        request = _order_to_request(order, requester)
        suppliers = list(
            session.scalars(
                select(Supplier).where(Supplier.status == "approved")
            )
        )
        events.emit(
            order_id,
            events.RUN_STARTED,
            f"Working from the structured request for {request.request_id} — "
            f"{len(suppliers)} approved supplier site"
            f"{'s' if len(suppliers) != 1 else ''} to read.",
        )

        offers: list[OfferEvidence] = []
        for supplier in suppliers:
            events.emit(
                order_id,
                events.VISIT,
                f"Reading {supplier.name}'s catalogue…",
                url=supplier.catalog_url or supplier.origin,
                supplier_id=supplier.id,
            )
            try:
                evidences = await research_supplier(
                    request, supplier, order_id=order_id
                )
            except Exception as exc:  # a broken site must not kill the run
                logger.warning("research failed for %s: %s", supplier.name, exc)
                events.emit(
                    order_id,
                    events.SUPPLIER_FAILED,
                    f"{supplier.name}: site unreadable ({exc.__class__.__name__})",
                    supplier_id=supplier.id,
                )
                continue
            for evidence in evidences:
                _persist_evidence(session, supplier, evidence)
                offers.append(evidence)
            events.emit(
                order_id,
                events.SUPPLIER_DONE,
                f"{supplier.name}: {len(evidences)} matching offer"
                f"{'s' if len(evidences) != 1 else ''} extracted",
                supplier_id=supplier.id,
                count=len(evidences),
            )

        events.emit(
            order_id,
            events.EVALUATING,
            f"Checking {len(offers)} offer"
            f"{'s' if len(offers) != 1 else ''} against your request and the "
            "purchasing policy (deterministic rules, not the model).",
        )
        results = [validate_offer(request, offer) for offer in offers]
        comparison = compare_offers(request, offers, results)

        n_pass = sum(1 for r in results if r.status.value == "PASS")
        n_unknown = sum(1 for r in results if r.status.value == "UNKNOWN")
        n_fail = sum(1 for r in results if r.status.value == "FAIL")
        events.emit(
            order_id,
            events.CHECKED,
            f"{n_pass} eligible, {n_unknown} uncertain, {n_fail} not eligible",
            counts={"PASS": n_pass, "UNKNOWN": n_unknown, "FAIL": n_fail},
        )

        session.expire_all()
        order = order_service.get_order(session, order_id)
        if comparison.selected_offer_id is None:
            events.emit(
                order_id,
                events.NO_OFFER,
                "No offer met every requirement — nothing can be recommended.",
            )
            order_service.transition(session, order, order_service.OrderStatus.NO_OFFER)
            return

        # Map the selected offer back to its product row.
        selected_supplier_id, _, selected_key = comparison.selected_offer_id.rpartition(":")
        product = (
            session.query(Product)
            .join(Supplier, Product.supplier_id == Supplier.id)
            .filter(
                Supplier.id == int(selected_supplier_id),
                Product.product_key == selected_key,
            )
            .one_or_none()
        )
        if product is None:  # pragma: no cover - defensive
            order_service.transition(session, order, order_service.OrderStatus.NO_OFFER)
            return

        order.selected_product_id = product.id
        session.commit()
        names = {str(s.id): s.name for s in suppliers}
        chosen = next(
            (o for o in offers if o.offer_id == comparison.selected_offer_id), None
        )
        selected_result = next(
            (r for r in results if r.offer_id == comparison.selected_offer_id), None
        )
        if chosen is not None:
            total_minor = (
                selected_result.verified_total_minor if selected_result else None
            )
            total_text = (
                format_hkd(total_minor) if total_minor is not None else "total unverified"
            )
            message = (
                f"Best compliant offer: {chosen.model_name or chosen.product_id} "
                f"from {names.get(chosen.supplier_id, 'supplier')} — {total_text}, "
                f"lowest verified total of the {len(comparison.ranked_offers)} "
                "eligible offers"
            )
        else:  # pragma: no cover - defensive
            message = "Best compliant offer selected."
        events.emit(
            order_id,
            events.SELECTED,
            message,
            offer_id=comparison.selected_offer_id,
            model=chosen.model_name if chosen else None,
        )
        order_service.transition(
            session, order, order_service.OrderStatus.PENDING_APPROVAL
        )


def run_research(engine, order_id: int) -> None:
    """Synchronous entry point (for a background thread)."""
    try:
        asyncio.run(_research(engine, order_id))
    except Exception:
        logger.exception("research for order %s crashed", order_id)
        events.emit(
            order_id,
            events.CRASHED,
            "The research run failed — the order was closed with no offer.",
        )
        # No compliant outcome is provable when the run itself failed.
        Session = session_factory(engine)
        with Session() as session:
            order = order_service.get_order(session, order_id)
            if order and order.status == order_service.OrderStatus.RESEARCHING.value:
                try:
                    order_service.transition(
                        session, order, order_service.OrderStatus.NO_OFFER
                    )
                except order_service.TransitionError:  # pragma: no cover
                    pass


def start_research_thread(engine, order_id: int) -> threading.Thread:
    """Spawn the blackbox research run for an order (fire-and-forget)."""
    thread = threading.Thread(
        target=run_research,
        args=(engine, order_id),
        name=f"research-order-{order_id}",
        daemon=True,
    )
    thread.start()
    return thread