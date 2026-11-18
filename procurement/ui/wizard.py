"""Page 1 — Order Request: a single-screen request builder.

Type what you need in your own words. The sentence is read by the same
intake parser the chatbot uses (one LLM call when the text changes), which
fills the form — category, specs, must-have words, quantity, budget. The
request is matched onto the typed catalog when it can be (deterministic
detection — the specs you state become checks; the rest are *no preference*
and never enforced) and routes to the live keyword-search flow when it
can't. A DRAFT order row autosaves behind the screen so a half-finished
request survives a refresh.
"""

from __future__ import annotations

import asyncio
import json

import streamlit as st

from .. import order_service
from ..catalog import BOOL, CATEGORIES, INT, LIST, OTHER_CATEGORY, detect_category
from ..llm.adapter import ConfigurableLLM
from ..repository import session_factory
from ..research import start_research_thread
from ..schemas import RequestDraft
from .common import pretty_requirements, render_order_status, status_badge

NO_PREFERENCE = "No preference"


def _reset_wizard() -> None:
    st.session_state.wizard = {}
    st.session_state.wizard_order_id = None
    st.session_state.pop("wizard_parsed_text", None)


# ---------------------------------------------------------------------------
# Spec widgets — every spec has an explicit "No preference" state
# ---------------------------------------------------------------------------

def _attribute_widget(attr, current):
    """One catalog attribute as a widget with a visible no-preference state.

    ``None`` is stored for no preference — the same representation the rules
    engine already treats as "not requested, not enforced".
    """
    key = f"wizard_field_{attr.name}"
    if attr.choices is not None:
        if attr.type == LIST:
            picked = st.multiselect(
                f"{attr.display_name} (choose all you require)",
                list(attr.choices),
                default=current or [],
                key=key,
                help="Leave empty for no preference.",
            )
            return picked or None
        options = [NO_PREFERENCE, *attr.choices]
        default = current if current in options else NO_PREFERENCE
        picked = st.selectbox(
            attr.display_name, options, index=options.index(default), key=key
        )
        return None if picked == NO_PREFERENCE else picked
    if attr.type == INT:
        on = st.toggle(
            f"Set a minimum {attr.display_name}",
            value=current is not None,
            key=f"{key}_on",
        )
        if not on:
            return None
        return st.number_input(
            attr.display_name, min_value=0, value=int(current or 0), key=key
        )
    if attr.type == BOOL:
        options = [NO_PREFERENCE, "Yes", "No"]
        default = {None: NO_PREFERENCE, True: "Yes", False: "No"}[current]
        picked = st.radio(
            attr.display_name,
            options,
            horizontal=True,
            index=options.index(default),
            key=key,
        )
        return {NO_PREFERENCE: None, "Yes": True, "No": False}[picked]
    return st.text_input(attr.display_name, value=current or "", key=key) or None


def _warranty_widget(current):
    """Minimum warranty — a toggle replaces the old '0 means any' hack."""
    on = st.toggle(
        "Require a minimum warranty",
        value=current is not None,
        key="wizard_field_warranty_on",
    )
    if not on:
        return None
    return st.number_input(
        "Minimum warranty (months)",
        min_value=0,
        value=int(current or 12),
        key="wizard_field_warranty",
    )


# ---------------------------------------------------------------------------
# Category resolution
# ---------------------------------------------------------------------------

def _resolve_category(data: dict) -> str:
    """Explicit override wins; otherwise detect from the item text.

    Detection never blocks: an unmatched item routes to the keyword-search
    flow, which works for anything the agent can find on a supplier site.
    """
    override = data.get("category_override")
    if override in CATEGORIES:
        return override
    if override == OTHER_CATEGORY:
        return OTHER_CATEGORY
    return detect_category(data.get("item_text")) or OTHER_CATEGORY


# ---------------------------------------------------------------------------
# Draft persistence
# ---------------------------------------------------------------------------

def _save_autosave(engine, user: dict, data: dict) -> int:
    """Create or refresh the DRAFT order row backing this wizard run."""
    Session = session_factory(engine)
    with Session() as session:
        order = (
            order_service.get_order(session, st.session_state.wizard_order_id)
            if st.session_state.get("wizard_order_id")
            else None
        )
        kwargs = dict(
            department=data.get("department") or "",
            category=data["category"],
            quantity=int(data.get("quantity") or 1),
            budget_minor=int(data.get("budget_minor") or 0),
            deadline=None,  # needed-by is collected later, when contacting the supplier
            requirements={
                "category": data["category"],
                "attributes": data.get("attributes") or {},
                "warranty_months_min": data.get("warranty_months_min"),
            },
            preferences={},
        )
        if order is not None:
            order_service.update_draft_order(session, order, **kwargs)
            order_id = order.id
        else:
            order = order_service.create_order(
                session, requester_id=user["id"], source="wizard", **kwargs
            )
            order_id = order.id
            st.session_state.wizard_order_id = order_id
    return order_id


def _widget_state_for(name: str, value, category: str):
    """Translate a stored attribute value into its widget's state value.

    Values may come from a resumed draft (already typed) or from the intake
    parser (which can return strings for int/bool attributes) — both are
    coerced to what the widget expects, with uncastable values degrading to
    the no-preference state instead of crashing the render.
    """
    spec = CATEGORIES.get(category)
    attr = spec.attribute_map.get(name) if spec else None
    if attr is None:
        return value
    if attr.choices is not None:
        if attr.type == LIST:
            return value or []
        return value if value in attr.choices else NO_PREFERENCE
    if attr.type == INT:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0  # the toggle stays off, so the stored value is ignored
    if attr.type == BOOL:
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return {"yes": "Yes", "no": "No"}.get(
            str(value or "").strip().lower(), NO_PREFERENCE
        )
    return value


def _seed_widget_state(data: dict, *, seed_item_text: bool = True) -> None:
    """Seed widget states from a resumed draft (Streamlit-sanctioned way).

    ``seed_item_text=False`` when the item-text widget is already on screen
    (the parse path): its session key cannot be written after instantiation.
    """
    if seed_item_text:
        st.session_state["wizard_item_text"] = data.get("item_text") or ""
    st.session_state["wizard_field_quantity"] = int(data.get("quantity") or 1)
    st.session_state["wizard_field_budget"] = float(data.get("budget_minor") or 0) / 100

    category = data.get("category") or OTHER_CATEGORY
    attrs = data.get("attributes") or {}
    if category == OTHER_CATEGORY:
        st.session_state["wizard_field_item"] = attrs.get("item") or ""
        st.session_state["wizard_field_keywords"] = ", ".join(attrs.get("keywords") or [])
    else:
        for name, value in attrs.items():
            st.session_state[f"wizard_field_{name}"] = _widget_state_for(name, value, category)
            if name in (CATEGORIES.get(category).attribute_map if CATEGORIES.get(category) else {}):
                attr = CATEGORIES[category].attribute_map[name]
                if attr.type == INT:
                    st.session_state[f"wizard_field_{name}_on"] = value is not None
    warranty = data.get("warranty_months_min")
    st.session_state["wizard_field_warranty_on"] = warranty is not None
    if warranty is not None:
        st.session_state["wizard_field_warranty"] = int(warranty)


# ---------------------------------------------------------------------------
# Intake parsing — the same reader the chatbot uses, filling this form
# ---------------------------------------------------------------------------

def _coerce_stored_value(attr, value):
    """Canonical *stored* representation of a parsed attribute value.

    The rules engine compares typed values (ints, bools, lists); the parser
    may return strings. Uncastable values become ``None`` (no preference)
    rather than crashing a later comparison.
    """
    if value is None or value == "":
        return None
    if attr.type == INT:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if attr.type == BOOL:
        if isinstance(value, bool):
            return value
        return {"yes": True, "true": True, "no": False, "false": False}.get(
            str(value).strip().lower()
        )
    if attr.type == LIST:
        if isinstance(value, (list, tuple)):
            return [str(v).strip() for v in value if str(v).strip()]
        return [v.strip() for v in str(value).split(",") if v.strip()]
    return str(value).strip()


def _draft_to_wizard_data(draft: RequestDraft, item_text: str, current: dict) -> dict:
    """Map an intake-parsed draft onto the wizard state (pure; unit-testable).

    Same shape a resumed draft produces. Values the user already set are
    kept when the parse leaves them unstated — the parser fills, it never
    erases.
    """
    category = draft.category
    raw_attrs = draft.attributes or {}
    attributes: dict = {}
    if category in CATEGORIES:
        spec = CATEGORIES[category]
        for attr in spec.attributes:
            value = raw_attrs.get(attr.name, raw_attrs.get(attr.display_name))
            value = _coerce_stored_value(attr, value) if value is not None else None
            if value is not None:
                attributes[attr.name] = value
    elif category == OTHER_CATEGORY:
        # item/keywords/search_query kept verbatim — the parser distilled them.
        attributes = {
            k: v for k, v in raw_attrs.items() if v not in (None, "", [])
        }

    budget_minor = current.get("budget_minor")
    if draft.max_total_hkd:
        budget_minor = int(round(float(draft.max_total_hkd) * 100))

    return {
        "item_text": item_text,
        "category": category,
        "category_override": category,  # None → fall back to auto-detection
        "attributes": attributes,
        "warranty_months_min": draft.warranty_months_min,
        "quantity": draft.quantity or current.get("quantity") or 1,
        "budget_minor": budget_minor,
        "department": current.get("department") or "",
    }


def _parse_request(text: str) -> RequestDraft | None:
    """Run the intake parser over the typed sentence; ``None`` on any failure."""
    async def _run() -> RequestDraft:
        return await ConfigurableLLM().update_draft(text, None)

    try:
        return asyncio.run(_run())
    except Exception:
        return None


def _maybe_parse_request(item_text: str, data: dict) -> None:
    """Read the typed request in the user's words and fill the form.

    One parse per distinct text (the marker prevents re-parse loops when the
    user edits the parsed fields). A failed parse leaves the manual flow
    exactly as it was. On success the wizard state is replaced and the page
    reruns with every widget seeded.
    """
    if not item_text or item_text == st.session_state.get("wizard_parsed_text"):
        return
    draft = None
    with st.spinner("Reading your request…"):
        draft = _parse_request(item_text)
    # Mark either way: never hammer the parser on unrelated reruns. Re-editing
    # the text clears the guard and parses again.
    st.session_state.wizard_parsed_text = item_text
    if draft is None:
        st.session_state.wizard_parse_failed = True
        return
    st.session_state.pop("wizard_parse_failed", None)
    parsed = _draft_to_wizard_data(draft, item_text, data)
    st.session_state.wizard = parsed
    _seed_widget_state(parsed, seed_item_text=False)
    st.session_state.wizard_parse_flash = True
    st.rerun()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(data: dict, category: str) -> list[str]:
    errors: list[str] = []
    if category == OTHER_CATEGORY:
        attrs = data.get("attributes") or {}
        if not attrs.get("item"):
            errors.append("Describe the item you need.")
        if not attrs.get("keywords"):
            errors.append(
                "Add at least one must-have word — listings without all of "
                "them are rejected."
            )
    return errors


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render(engine, user: dict) -> None:
    st.title("New Order Request")

    st.session_state.setdefault("wizard", {})
    st.session_state.setdefault("wizard_order_id", None)

    # ---- just-submitted order: keep the live run console on screen ----------
    submitted_id = st.session_state.get("wizard_submitted_order")
    if submitted_id is not None:
        Session = session_factory(engine)
        with Session() as session:
            submitted = order_service.get_order(session, submitted_id)
        if submitted is None or submitted.status == order_service.OrderStatus.DRAFT.value:
            st.session_state.pop("wizard_submitted_order", None)
        else:
            st.title("Order request submitted")

            render_order_status(engine, submitted_id)
            if st.button("Start another request"):
                st.session_state.pop("wizard_submitted_order", None)
                st.rerun()
            return

    data = st.session_state.wizard

    # ---- resumable drafts -----------------------------------------------------
    Session = session_factory(engine)
    with Session() as session:
        drafts = order_service.user_drafts(session, user["id"])
    if drafts and not data:
        with st.expander(f"Resume a draft ({len(drafts)})", expanded=False):
            for d in drafts:
                col1, col2, col3 = st.columns([3, 1, 1])
                col1.markdown(
                    f"{d.order_no} — {d.category or '—'} × {d.quantity or '?'} "
                    f"{status_badge(d.status)}",
                    unsafe_allow_html=True,
                )
                if col2.button("Continue", key=f"resume_{d.id}"):
                    Session2 = session_factory(engine)
                    with Session2() as s2:
                        order = order_service.get_order(s2, d.id)
                        reqs = json.loads(order.requirements_json or "{}")
                        attrs = reqs.get("attributes", {})
                        spec = CATEGORIES.get(order.category)
                        st.session_state.wizard = {
                            "item_text": (
                                attrs.get("item") or ""
                                if order.category == OTHER_CATEGORY
                                else (spec.label if spec else "")
                            ),
                            "category": order.category,
                            "category_override": order.category,
                            "attributes": attrs,
                            "warranty_months_min": reqs.get("warranty_months_min"),
                            "quantity": order.quantity,
                            "budget_minor": order.budget_minor,
                        }
                        st.session_state.wizard_order_id = order.id
                        _seed_widget_state(st.session_state.wizard)
                    st.rerun()
                if col3.button("Discard", key=f"discard_{d.id}"):
                    Session2 = session_factory(engine)
                    with Session2() as s2:
                        order_service.discard_draft(s2, order_service.get_order(s2, d.id))
                    # Clear the in-memory wizard state too — otherwise the
                    # leftover values would autosave a fresh draft identical
                    # to the one just discarded.
                    if st.session_state.get("wizard_order_id") == d.id or not (
                        st.session_state.get("wizard") or {}
                    ):
                        _reset_wizard()
                    st.rerun()

    # ---- what do you need -------------------------------------------------
    item_text = st.text_input(
        "What do you need?",
        value=data.get("item_text") or "",
        key="wizard_item_text",
        placeholder="",
    )
    data["item_text"] = item_text

    # One LLM read per distinct sentence fills the form below; edits to the
    # parsed fields never re-trigger it, and a failure leaves the manual flow.
    _maybe_parse_request(item_text, data)
    if st.session_state.pop("wizard_parse_flash", False):
        st.caption(
            "I read your request and filled the fields below — adjust "
            "anything I got wrong."
        )
    elif st.session_state.pop("wizard_parse_failed", False):
        st.caption(
            "I couldn't read that automatically — set the fields below yourself."
        )

    category = _resolve_category(data)
    if data.get("category") != category:
        data["category"] = category
        data["attributes"] = {}  # fresh spec set per category

    if category != OTHER_CATEGORY:
        spec = CATEGORIES[category]
        st.success(
            f"Recognised as **{spec.label}** — offers will be checked against "
            "the specs you set below. Anything left as *No preference* "
            "won't be enforced."
        )

    # ---- specifications (all optional) ------------------------------------
    st.subheader("Specifications")
    if category == OTHER_CATEGORY:
        st.caption(
            "I'll search supplier sites for this and keep only the listings "
            "that mention every one of your must-have words."
        )
        # The parser distilled the typed sentence into a product name — the
        # order record and review panel carry that, never the raw wording.
        parsed_item = str((data.get("attributes") or {}).get("item") or "").strip()
        item = st.text_input(
            "Product",
            value=parsed_item or item_text,
            key="wizard_field_item",
            help="The product name as it should appear on the order.",
        )
        keywords_csv = st.text_input(
            "Must-have words",
            value=", ".join((data.get("attributes") or {}).get("keywords", []) or []),
            key="wizard_field_keywords",
            placeholder="e.g. thinkpad, 512gb",
            help=(
                "Reject any listing that doesn't mention every one of these. "
                "Add only what an offer must really contain — the more you "
                "add, the fewer listings survive."
            ),
        )
        attributes = {
            "item": item.strip() or item_text.strip(),
            "keywords": [k.strip() for k in keywords_csv.split(",") if k.strip()],
        }
        # Keep the parser's distilled search query (when one was parsed) so
        # research searches "laptop" instead of the whole typed sentence.
        search_query = (data.get("attributes") or {}).get("search_query")
        if search_query:
            attributes["search_query"] = search_query
        data["attributes"] = attributes
    else:
        st.caption(
            "All optional — leave anything you don't care about as *No preference*."
        )
        spec = CATEGORIES[category]
        attributes = {}
        for attr in spec.attributes:
            attributes[attr.name] = _attribute_widget(
                attr, data.get("attributes", {}).get(attr.name)
            )
        data["attributes"] = attributes
    data["warranty_months_min"] = _warranty_widget(data.get("warranty_months_min"))

    # ---- commercial terms ---------------------------------------------------
    st.subheader("Commercial terms")
    data["quantity"] = st.number_input(
        "How many units?",
        min_value=1,
        value=int(data.get("quantity") or 1),
        key="wizard_field_quantity",
    )
    budget_major = st.number_input(
        "Maximum total budget (HKD)",
        min_value=0.0,
        value=float(data.get("budget_minor", 0)) / 100 or 0.0,
        step=100.0,
        key="wizard_field_budget",
    )
    data["budget_minor"] = int(round(budget_major * 100))
    # Department is assigned from the requester's account, not asked here.
    data["department"] = user.get("department") or ""

    # ---- live review + submit ------------------------------------------------
    if not item_text:
        st.caption("Live summary")
        return

    st.subheader("Review")
    with st.container(border=True):
        order_id = _save_autosave(engine, user, data)
        Session = session_factory(engine)
        with Session() as session:
            order = order_service.get_order(session, order_id)
            st.markdown(
                f"**{order.order_no}** — {status_badge(order.status)}",
                unsafe_allow_html=True,
            )
        requirements = {
            "category": category,
            "attributes": data.get("attributes") or {},
            "warranty_months_min": data.get("warranty_months_min"),
        }
        st.markdown(pretty_requirements(json.dumps(requirements)))
        st.markdown(
            f"- **Quantity:** {data.get('quantity')}\n"
            f"- **Budget (max):** HK${(data.get('budget_minor') or 0) / 100:,.2f}\n"
            f"- **Department:** {data.get('department') or '—'}"
        )
        if category != OTHER_CATEGORY:
            spec = CATEGORIES[category]
            unset = sum(1 for a in spec.attributes if not data.get("attributes", {}).get(a.name))
            if unset:
                st.caption(
                    f"{unset} spec"
                    f"{'s' if unset != 1 else ''} left as no preference — "
                    "they won't be enforced against offers."
                )
        st.caption(
            "On submit, the agent browses every approved supplier site, "
            "extracts and verifies offers, and queues the best compliant one "
            "for approval."
        )

    if st.button("Submit order request", type="primary", use_container_width=True):
        errors = _validate(data, category)
        if errors:
            st.error(" ".join(errors))
            st.stop()
        Session = session_factory(engine)
        with Session() as session:
            order = order_service.get_order(session, order_id)
            order_service.submit_order(session, order)
            start_research_thread(engine, order.id)
            st.session_state.wizard_submitted_order = order.id
        _reset_wizard()
        st.rerun()