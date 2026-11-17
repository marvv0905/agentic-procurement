"""Page 2 — All Orders: read-only history for everyone.

Table of all orders with filters and a detail view including the decision
trail. No action buttons by design — approvals live on their own page.
"""

from __future__ import annotations

import streamlit as st

from .. import order_service
from ..repository import session_factory
from .common import hkd, pretty_requirements, status_badge, status_label


def render(engine, user: dict) -> None:
    st.title("All Orders")
    st.caption("Read-only history of every order request — everyone sees all orders.")

    Session = session_factory(engine)
    with Session() as session:
        orders = order_service.list_orders(session)

    if not orders:
        st.info(
            "No orders yet. Create one from the Chatbot or the Order Request page."
        )
        return

    # ---- rows (built once; every filter derives its options from the data) ----
    Session = session_factory(engine)
    with Session() as session:
        items = []  # (order, row) pairs, kept in step so filters and detail agree
        for o in orders:
            view = order_service.order_view(session, o)
            items.append(
                (
                    o,
                    {
                        "Order": o.order_no,
                        "Status": status_label(o.status),
                        "Category": o.category or "—",
                        "Qty": o.quantity,
                        "Budget": o.budget_minor / 100,
                        "Supplier": view.supplier.name if view.supplier else "—",
                        "Product": view.product.model_name if view.product else "—",
                        "Total": (
                            view.total_minor / 100
                            if view.total_minor is not None
                            else None
                        ),
                        "Requester": (
                            view.requester.display_name if view.requester else "—"
                        ),
                        "Department": o.department or "—",
                        "Updated": o.updated_at.strftime("%Y-%m-%d %H:%M"),
                    },
                )
            )

    # ---- filters: column slices over the data, plus the grid's built-in
    # search, column sort and pinned-header scroll --------------------------
    status_map = {s.value: status_label(s.value) for s in order_service.OrderStatus}
    categories = sorted({r["Category"] for _, r in items if r["Category"] != "—"})
    suppliers = sorted({r["Supplier"] for _, r in items if r["Supplier"] != "—"})
    requesters = sorted({r["Requester"] for _, r in items if r["Requester"] != "—"})
    departments = sorted({r["Department"] for _, r in items if r["Department"] != "—"})

    col1, col2, col3, col4, col5 = st.columns(5)
    status_filter = col1.selectbox(
        "Status",
        ["All", *status_map.keys()],
        format_func=lambda v: "All" if v == "All" else status_map[v],
    )
    category_filter = col2.selectbox("Category", ["All", *categories])
    supplier_filter = col3.selectbox("Supplier", ["All", *suppliers])
    requester_filter = col4.selectbox("Requester", ["All", *requesters])
    department_filter = col5.selectbox("Department", ["All", *departments])

    def _keep(item) -> bool:
        o, r = item
        if status_filter != "All" and o.status != status_filter:
            return False
        if category_filter != "All" and r["Category"] != category_filter:
            return False
        if supplier_filter != "All" and r["Supplier"] != supplier_filter:
            return False
        if requester_filter != "All" and r["Requester"] != requester_filter:
            return False
        if department_filter != "All" and r["Department"] != department_filter:
            return False
        return True

    shown = [item for item in items if _keep(item)]
    if not shown:
        st.info("No orders match these filters. Clear them to see all orders.")
        return

    # ---- summary table ------------------------------------------------------
    # Fixed-height grid: the page stops lengthening as orders grow, the
    # header stays pinned while rows scroll, and the grid's toolbar search
    # and click-to-sort need no rerun.
    meta, refresh = st.columns([4, 1])
    meta.caption(f"{len(shown)} of {len(items)} orders, newest first")
    refresh.button("Refresh", use_container_width=True)

    st.dataframe(
        [r for _, r in shown],
        height=420,
        hide_index=True,
        column_config={
            "Budget": st.column_config.NumberColumn(format="HK$ %,.2f"),
            "Total": st.column_config.NumberColumn(format="HK$ %,.2f"),
        },
        alt="All orders with status, budget, supplier and total columns",
    )

    # ---- detail view ----------------------------------------------------------
    st.subheader("Order details")
    labels = {
        o.id: f"{o.order_no} · {o.category or '—'} · {status_label(o.status)}"
        for o, _ in shown
    }
    picked_id = st.selectbox(
        "Select an order",
        [o.id for o, _ in shown],
        format_func=lambda oid: labels[oid],
    )
    order = next(o for o, _ in shown if o.id == picked_id)
    Session = session_factory(engine)
    with Session() as session:
        view = order_service.order_view(session, order)

    st.markdown(
        f"### {order.order_no} &nbsp; {status_badge(order.status)}",
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Requested**")
        st.markdown(pretty_requirements(order.requirements_json))
        st.markdown(
            f"- **Quantity:** {order.quantity}\n"
            f"- **Budget (max):** {hkd(order.budget_minor)}\n"
            f"- **Needed by:** {order.deadline or '— (decide later)'}\n"
            f"- **Requester:** "
            f"{view.requester.display_name if view.requester else '—'} "
            f"({order.department})\n"
            f"- **Source:** {order.source}\n"
            f"- **Created:** {order.created_at.strftime('%Y-%m-%d %H:%M')}"
        )
    with col2:
        st.markdown("**Selected offer**")
        if view.product is None:
            st.markdown("— (none selected yet)")
        else:
            st.markdown(
                f"- **Supplier:** {view.supplier.name if view.supplier else '—'}\n"
                f"- **Product:** {view.product.model_name or view.product.product_key}\n"
                f"- **Unit price:** {hkd(view.product.unit_price_minor)}\n"
                f"- **Shipping:** {hkd(view.product.shipping_minor)}\n"
                f"- **Total ({order.quantity} units):** {hkd(view.total_minor)}\n"
                f"- **Warranty:** "
                f"{view.product.warranty_months or '—'} months\n"
                f"- **Delivery:** "
                f"{view.product.delivery_days if view.product.delivery_days is not None else '—'} days"
            )

    if view.decisions:
        st.markdown("**Decision trail**")
        for d in view.decisions:
            st.markdown(
                f"- {d.decided_at.strftime('%Y-%m-%d %H:%M')} — "
                f"**{d.decision.title()}** by a {d.role}"
                + (f" — {d.reason}" if d.reason else "")
            )