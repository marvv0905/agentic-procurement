"""The run console — one order's live cockpit.

Three panels, ported from the PurchasePilot agent-console design:

- **What the agent understood** — the parsed request as a read-back list.
- **What the agent is doing** — a browser viewer (which site the agent is
  reading) plus a step track fed by the ephemeral :mod:`procurement.events`
  store. When the run predates the process (feed gone) the panels degrade to
  status-derived steps.
- **Offers found** — PASS / UNKNOWN / FAIL stamps with reasons, rebuilt by
  re-running the deterministic rules over the persisted products (so the
  panel is correct even after a restart), ending in the requisition slip
  when a compliant offer was selected.

While the order is RESEARCHING the console reruns itself every two seconds
inside an ``st.fragment``: the rest of the page stays interactive, the
frame updates live for however long research takes, and the final frame
(with the slip) appears the moment the run finishes — no manual refresh.

View-only by design: approve/reject live on the Pending Approvals page.
"""

from __future__ import annotations

import csv
import html
import io

import streamlit as st

from .. import events, frames, order_service
from ..evaluation import counts as verdict_counts
from ..evaluation import order_verdicts
from ..money import format_hkd
from ..repository import session_factory
from ..rules import QUOTATION_TARGET
from .common import pretty_requirements_pairs, status_badge, status_label

_RESEARCHING = order_service.OrderStatus.RESEARCHING.value
_SELECTED_STATUSES = (
    order_service.OrderStatus.PENDING_APPROVAL.value,
    order_service.OrderStatus.APPROVED.value,
    order_service.OrderStatus.PLACED.value,
    order_service.OrderStatus.REJECTED.value,
)

_esc = html.escape

_STEPS = (
    ("read", "Read your request", "Turn your message into a checkable request"),
    ("visit", "Visit the approved suppliers", "Read every matching listing on each site"),
    (
        "check",
        "Check offers against policy",
        "Specs, warranty, stock, budget — deterministic rules, not the model",
    ),
    (
        "recommend",
        "Recommend and prepare the requisition",
        "Lowest verified total that meets every requirement",
    ),
)

_LOG_STEP = {
    events.RUN_STARTED: "read",
    events.SUPPLIER_DONE: "visit",
    events.SUPPLIER_FAILED: "visit",
    events.CHECKED: "check",
    events.SELECTED: "recommend",
    events.NO_OFFER: "recommend",
    events.CRASHED: "recommend",
}
_LOG_KIND = {
    events.SUPPLIER_DONE: "found",
    events.SUPPLIER_FAILED: "warn",
    events.CHECKED: "found",
    events.SELECTED: "found",
    events.NO_OFFER: "fail",
    events.CRASHED: "fail",
}


# ---------------------------------------------------------------------------
# Public HTML components (also used by the approvals page)
# ---------------------------------------------------------------------------

def offers_html(verdicts) -> str:
    """The evidence list: one row per offer with its stamp and reasons."""
    if not verdicts:
        return (
            '<p class="pp-empty">Offers appear here as the agent reads each '
            "supplier's site.</p>"
        )
    items = []
    for v in verdicts:
        p = v.product
        stock = (
            f"{p.available_quantity} in stock"
            if p.available_quantity is not None
            else "stock unknown"
        )
        warranty = (
            f"{p.warranty_months} mo warranty"
            if p.warranty_months is not None
            else "warranty unknown"
        )
        delivery = (
            f"delivery ~{p.delivery_days} days"
            if p.delivery_days is not None
            else "delivery unknown"
        )
        if v.total_minor is not None:
            price = format_hkd(v.total_minor)
        elif p.unit_price_minor is not None:
            price = format_hkd(p.unit_price_minor)
        else:
            price = "—"
        stamps = f'<span class="pp-stamp pp-stamp--{v.status}">{v.status}</span>'
        if v.recommended:
            stamps += '<span class="pp-stamp pp-stamp--REC">Recommended</span>'
        why = (
            f'<span class="pp-offer__why">{_esc("; ".join(v.issues))}</span>'
            if v.issues
            else ""
        )
        items.append(
            f'<li class="pp-offer{" pp-offer--unknown" if v.status == "UNKNOWN" else ""}">'
            f'<span class="pp-offer__name">{_esc(v.model_name)}{stamps}</span>'
            f'<span class="pp-offer__price">{_esc(price)}</span>'
            f'<span class="pp-offer__meta">'
            f"{_esc(v.supplier_name)} · {_esc(stock)} · {_esc(warranty)} · "
            f"{_esc(delivery)}</span>"
            f"{why}"
            "</li>"
        )
    return f'<ul style="list-style:none;margin:0;padding:0;">{"".join(items)}</ul>'


def slip_html(order, view, pass_count: int) -> str:
    """The requisition slip — the run console's signature piece."""
    product, supplier = view.product, view.supplier
    if product is None:
        return ""
    total = view.total_minor
    model = product.model_name or product.product_key
    supplier_name = supplier.name if supplier else "—"

    notes: list[str] = []
    if product.shipping_minor is None and total is not None:
        notes.append("Shipping was not listed by the site; the total excludes it.")
    if pass_count < QUOTATION_TARGET:
        notes.append(
            f"Only {pass_count} fully verified quote"
            f"{'s' if pass_count != 1 else ''}; policy asks for "
            f"{QUOTATION_TARGET}. The approver will see this."
        )
    if total is not None and total > order.budget_minor:
        notes.append("The total exceeds the stated budget.")

    eligible_phrase = (
        f" of the {pass_count} eligible offers" if pass_count > 1 else ""
    )
    says = (
        f"{model} from {supplier_name} meets every requirement and has the "
        f"lowest verified total{eligible_phrase}."
    )

    status = order.status
    if status in (
        order_service.OrderStatus.APPROVED.value,
        order_service.OrderStatus.PLACED.value,
    ):
        stamp = '<span class="pp-approved">Approved &amp; submitted</span>'
    elif status == order_service.OrderStatus.REJECTED.value:
        stamp = (
            '<span class="pp-approved" '
            'style="border-color:var(--pp-fail);color:var(--pp-fail)">Rejected</span>'
        )
    else:
        stamp = ""

    def _dd(value: str) -> str:
        return f"<dd>{_esc(value)}</dd>"

    rows = (
        ("<dt>Item</dt>", _dd(model)),
        ("<dt>Supplier</dt>", _dd(supplier_name)),
        (f"<dt>Unit price × {order.quantity}</dt>", _dd(format_hkd(product.unit_price_minor) if product.unit_price_minor is not None else "—")),
        ("<dt>Shipping</dt>", _dd(format_hkd(product.shipping_minor) if product.shipping_minor is not None else "not listed")),
        ("<dt>Verified total</dt>", f'<dd class="pp-slip__total">{_esc(format_hkd(total) if total is not None else "unverified")}</dd>'),
        ("<dt>Budget left</dt>", _dd(format_hkd(order.budget_minor - total) if total is not None else "—")),
        ("<dt>Warranty</dt>", _dd(f"{product.warranty_months} months" if product.warranty_months is not None else "unknown")),
        ("<dt>Delivery</dt>", _dd(f"~{product.delivery_days} days" if product.delivery_days is not None else "unknown")),
        ("<dt>Approval by</dt>", _dd("Your manager")),
    )
    dl = "".join(dt + dd for dt, dd in rows)
    notes_html = (
        f'<div class="pp-slip__notes">'
        + "".join(f"<p>{_esc(n)}</p>" for n in notes)
        + "</div>"
        if notes
        else ""
    )
    return (
        '<article class="pp-slip">'
        '<div class="pp-slip__header"><strong>Purchase requisition</strong>'
        f"<span>{_esc(order.order_no)} · {_esc(status_label(status))} {stamp}</span></div>"
        f'<p class="pp-slip__says">{_esc(says)}</p>'
        f"<dl>{dl}</dl>"
        f"{notes_html}"
        "</article>"
    )


def comparison_csv(verdicts) -> str:
    """The whole comparison — every offer the agent read and its verdict."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Offer",
            "Supplier",
            "Model",
            "Unit price (HKD)",
            "Shipping (HKD)",
            "Verified total (HKD)",
            "Stock",
            "Warranty (months)",
            "Delivery (days)",
            "Result",
            "Issues",
            "Recommended",
        ]
    )
    for v in verdicts:
        p = v.product
        writer.writerow(
            [
                p.product_key,
                v.supplier_name,
                v.model_name,
                p.unit_price_minor / 100 if p.unit_price_minor is not None else "",
                p.shipping_minor / 100 if p.shipping_minor is not None else "",
                v.total_minor / 100 if v.total_minor is not None else "",
                p.available_quantity if p.available_quantity is not None else "",
                p.warranty_months if p.warranty_months is not None else "",
                p.delivery_days if p.delivery_days is not None else "",
                v.status,
                "; ".join(v.issues),
                "yes" if v.recommended else "",
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Panel rendering
# ---------------------------------------------------------------------------

def _pairs_html(pairs: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"<li><span>{_esc(label)}</span><span>{_esc(str(value))}</span></li>"
        for label, value in pairs
    )
    return f'<ul class="pp-parsed">{rows}</ul>'


def _panel_request(order) -> None:
    with st.container(border=True):
        st.markdown(
            '<p class="pp-kicker">What the agent understood</p>',
            unsafe_allow_html=True,
        )
        pairs = pretty_requirements_pairs(order.requirements_json) + [
            ("Quantity", str(order.quantity)),
            ("Budget (max)", format_hkd(order.budget_minor)),
            ("Needed by", str(order.deadline) if order.deadline else "— (decide later)"),
            ("Department", order.department or "—"),
        ]
        st.markdown(_pairs_html(pairs), unsafe_allow_html=True)


def _doing_text(order, track) -> str:
    if track:
        return track[-1].message
    if order.status == _RESEARCHING:
        return (
            "Researching suppliers — this run started in a previous session, "
            "so live step details are no longer available."
        )
    if order.status == order_service.OrderStatus.PENDING_APPROVAL.value:
        return "Done. The requisition is ready for a manager to review."
    if order.status == order_service.OrderStatus.NO_OFFER.value:
        return "Done. No offer met every requirement."
    if order.status == order_service.OrderStatus.PLACED.value:
        return "Approved and submitted to the supplier."
    if order.status == order_service.OrderStatus.REJECTED.value:
        return "Rejected by the manager."
    return f"Order is {status_label(order.status).lower()}."


def _viewer_html(order, track) -> str:
    live = order.status == _RESEARCHING
    last_visit = next(
        (e for e in reversed(track) if e.kind == events.VISIT), None
    )
    url = (last_visit.detail.get("url") if last_visit else None) or "No page open"
    return (
        f'<div class="pp-viewer{" pp-viewer--live" if live else ""}">'
        '<div class="pp-viewer__url"><span class="pp-viewer__dot"></span>'
        f"<code>{_esc(url)}</code></div>"
        f'<div class="pp-viewer__doing">{_esc(_doing_text(order, track))}</div>'
        "</div>"
    )


def _track_html(order, track) -> str:
    has_eval = any(e.kind == events.EVALUATING for e in track)
    terminal = order.status != _RESEARCHING
    states = {
        "read": "done",
        "visit": "done" if (has_eval or terminal) else "working",
        "check": "done" if terminal else ("working" if has_eval else None),
        "recommend": None,
    }
    if order.status in _SELECTED_STATUSES:
        states["recommend"] = "done"
    elif order.status == order_service.OrderStatus.NO_OFFER.value:
        states["recommend"] = "skipped"

    subs = dict((sid, sub) for sid, _, sub in _STEPS)
    if states["recommend"] == "skipped":
        subs["recommend"] = "No compliant offer could be recommended"

    logs: dict[str, list[tuple[str, str]]] = {sid: [] for sid, _, _ in _STEPS}
    for event in track:
        step = _LOG_STEP.get(event.kind)
        if step is not None:
            logs[step].append((event.message, _LOG_KIND.get(event.kind, "")))

    items = []
    for index, (sid, title, default_sub) in enumerate(_STEPS):
        state = states[sid]
        cls = f" pp-step--{state}" if state else ""
        log_items = "".join(
            f'<li class="pp-log--{kind}">{_esc(message)}</li>'
            for message, kind in logs[sid]
        )
        items.append(
            f'<li class="pp-step{cls}">'
            f'<span class="pp-step__mark" aria-hidden="true">{index + 1}</span>'
            f'<div class="pp-step__title">{_esc(title)}</div>'
            f'<p class="pp-step__sub">{_esc(subs.get(sid, default_sub))}</p>'
            f'<ul class="pp-log">{log_items}</ul>'
            "</li>"
        )
    return f'<ol class="pp-track">{"".join(items)}</ol>'


def _panel_run(order, track) -> None:
    with st.container(border=True):
        st.markdown(
            '<p class="pp-kicker">What the agent is doing</p>',
            unsafe_allow_html=True,
        )
        frame = frames.latest(order.id)
        if frame is not None:
            st.image(frame.data, width="stretch")
            st.caption(
                "Agent browser view"
            )
        st.markdown(_viewer_html(order, track), unsafe_allow_html=True)
        st.markdown(_track_html(order, track), unsafe_allow_html=True)


def _filter_row(order_id: int, verdicts) -> str:
    counts = verdict_counts(verdicts)
    key = f"pp_filter_{order_id}"
    active = st.session_state.get(key, "all")
    options = (
        ("all", f"All {len(verdicts)}"),
        ("PASS", f"Eligible {counts['PASS']}"),
        ("UNKNOWN", f"Uncertain {counts['UNKNOWN']}"),
        ("FAIL", f"Not eligible {counts['FAIL']}"),
    )
    columns = st.columns(len(options))
    changed = active
    for column, (value, label) in zip(columns, options):
        if column.button(
            label,
            key=f"pp_filterbtn_{order_id}_{value}",
            use_container_width=True,
            type="primary" if active == value else "secondary",
        ):
            changed = value
    if changed != active:
        st.session_state[key] = changed
        st.rerun()
    return changed


def _panel_evidence(order_id: int, order, session) -> None:
    with st.container(border=True):
        st.markdown('<p class="pp-kicker">Offers found</p>', unsafe_allow_html=True)
        verdicts = order_verdicts(session, order)
        if not verdicts:
            st.markdown(
                '<p class="pp-empty">Offers appear here as the agent reads each '
                "supplier's site.</p>",
                unsafe_allow_html=True,
            )
            return

        active = _filter_row(order_id, verdicts)
        filtered = (
            verdicts
            if active == "all"
            else [v for v in verdicts if v.status == active]
        )
        st.markdown(
            offers_html(filtered) if filtered
            else '<p class="pp-empty">No offers in this group.</p>',
            unsafe_allow_html=True,
        )

        if order.selected_product_id:
            view = order_service.order_view(session, order)
            counts = verdict_counts(verdicts)
            st.markdown(
                slip_html(order, view, counts["PASS"]), unsafe_allow_html=True
            )
            st.download_button(
                "Download comparison",
                data=comparison_csv(verdicts),
                file_name=f"comparison_{order.order_no}.csv",
                mime="text/csv",
                key=f"pp_csv_{order_id}",
                use_container_width=True,
            )
        elif order.status == order_service.OrderStatus.NO_OFFER.value:
            st.error(
                "No offer met every requirement. Edit the request and submit "
                "it again from Order Request or the chat."
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _draw(engine, order_id: int) -> None:
    """Render one full frame of the console."""
    Session = session_factory(engine)
    with Session() as session:
        order = order_service.get_order(session, order_id)
        if order is None:
            st.warning("Order not found.")
            return
        track = events.events(order_id)

        st.markdown(
            f'<span style="font-size:1.3rem;font-weight:700;">'
            f"{_esc(order.order_no)}</span>&nbsp;&nbsp;{status_badge(order.status)}",
            unsafe_allow_html=True,
        )
        if order.status == order_service.OrderStatus.DRAFT.value:
            st.caption("Not submitted yet — nothing for the agent to do.")
            return

        left, middle, right = st.columns([1, 1.3, 1.2], gap="large")
        with left:
            _panel_request(order)
        with middle:
            _panel_run(order, track)
        with right:
            _panel_evidence(order_id, order, session)


def render(engine, order_id: int, poll_seconds: int = 30) -> None:
    """Live run console for one order (view-only; approvals live elsewhere).

    While the order is RESEARCHING, the console is wrapped in an
    ``st.fragment(run_every=2)``: it redraws itself every two seconds without
    blocking the rest of the page, for as long as research actually takes.
    When the run finishes, the fragment escapes via a full rerun, the
    terminal frame (with the requisition slip) renders on the static path,
    and the self-refresh timer stops. ``poll_seconds`` is kept for
    call-site compatibility and no longer used.
    """
    Session = session_factory(engine)
    with Session() as session:
        order = order_service.get_order(session, order_id)
    if order is None:
        st.warning("Order not found.")
        return
    if order.status != _RESEARCHING:
        _draw(engine, order_id)
        return

    @st.fragment(run_every=2)
    def _live() -> None:
        Session2 = session_factory(engine)
        with Session2() as session2:
            current = order_service.get_order(session2, order_id)
        if current is None or current.status != _RESEARCHING:
            # Research finished (or the order vanished): escape the
            # self-refresh timer. The full rerun renders the terminal frame
            # on the static path, so the fragment stops ticking.
            st.rerun(scope="app")
        _draw(engine, order_id)

    _live()