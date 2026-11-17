"""Page 0 — Chatbot: persistent LLM sessions that turn into orders.

Messages and the running ``RequestDraft`` are persisted per chat session, so
conversations survive reruns and refreshes. Confirming a draft creates an
order and hands it to the agent blackbox. The demo keeps 5 sessions globally —
the oldest is overwritten when a new one is created.
"""

from __future__ import annotations

import asyncio

import streamlit as st

from .. import chat_service, order_service, seller_inquiry
from ..catalog import CATEGORIES, OTHER_CATEGORY
from ..llm.adapter import ConfigurableLLM
from ..parsing import ClarificationNeeded, draft_to_request
from ..repository import session_factory
from ..research import start_research_thread
from ..schemas import RequestDraft
from .common import render_order_status

GREETING = (
    "Hi! I source office equipment — **monitors, keyboards, and chairs** — and "
    "I can search live supplier sites for anything else you describe, e.g.:\n\n"
    "> 6 monitors, 27 inch, 2560x1440, HDMI and DisplayPort, 3-year warranty, "
    "budget HK$8,000, deliver by 2026-11-20, for the engineering team.\n\n"
    "> Or something less standard: *noise cancelling headphones, bluetooth, "
    "budget HK$2,000, for the design team.*"
)


def _load_or_create_session(Session, user: dict):
    """Resolve the active chat session, reloading its draft from the DB."""
    if st.session_state.get("chat_session_id") is not None:
        row = chat_service.get_session_row(
            Session, st.session_state.chat_session_id
        )
        if row is not None:
            # Reload the working draft when the session changed.
            if st.session_state.get("draft_for_session") != row.id:
                st.session_state.draft_for_session = row.id
                data = chat_service.load_draft(Session, row)
                st.session_state.draft = (
                    RequestDraft.model_validate(data) if data else None
                )
            return row

    # No (valid) active session — make one.
    row = chat_service.create_session(Session, user_id=user["id"])
    st.session_state.chat_session_id = row.id
    st.session_state.draft_for_session = row.id
    st.session_state.draft = None
    return row


def _update_draft(text: str, current: RequestDraft | None) -> RequestDraft:
    async def _run() -> RequestDraft:
        llm = ConfigurableLLM()
        return await llm.update_draft(text, current)

    return asyncio.run(_run())


def _draft_markdown(d: RequestDraft, user: dict) -> str:
    lines = [
        f"- **Category:** {d.category or '—'}",
        f"- **Quantity:** {d.quantity or '—'}",
    ]
    if d.category == OTHER_CATEGORY:
        if d.attributes.get("item"):
            lines.append(f"- **Item:** {d.attributes['item']}")
        if d.attributes.get("keywords"):
            lines.append(
                f"- **Must-have words:** {', '.join(d.attributes['keywords'])}"
            )
    else:
        spec = CATEGORIES.get(d.category or "")
        if spec is not None:
            for attr in spec.attributes:
                value = d.attributes.get(attr.name)
                lines.append(
                    f"- **{attr.display_name}:** "
                    f"{value if value not in (None, '') else '—'}"
                )
    lines.append(f"- **Minimum warranty:** {d.warranty_months_min or '—'} months")
    lines.append(f"- **Budget (max):** HK${d.max_total_hkd}")
    lines.append(f"- **Needed by:** {d.deadline or '— (decide later)'}")
    lines.append(
        f"- **Department:** {d.department or user.get('department') or '—'} "
        "(from your account)"
    )
    if d.preferences:
        lines.append(f"- **Preferences:** {d.preferences}")
    return "\n".join(lines)


def _assistant(Session, row, text: str) -> None:
    chat_service.add_message(Session, row, role="assistant", content=text)
    if row.title == "New chat":
        messages = chat_service.list_messages(Session, row.id)
        first_user = next(
            (m.content for m in messages if m.role == "user"), None
        )
        if first_user:
            row.title = first_user[:40]
            Session.commit()


def _inquiry_key(row) -> str:
    return f"seller_inquiry_{row.id}"


def _offer_seller_contact(Session, row, prompt: str, pq) -> None:
    """Answer a product question briefly and offer to contact the seller."""
    parts = [pq.reply] if pq.reply else []
    parts.append(
        "The seller can confirm the details. **Would you like to contact the seller "
        "for more info?** Your request draft stays as it is either way."
    )
    msg = "\n\n".join(parts)
    with st.chat_message("assistant"):
        st.markdown(msg)
    _assistant(Session, row, msg)
    st.session_state[_inquiry_key(row)] = {
        "stage": "offer", "question": prompt, "product": pq.product,
    }


def _render_seller_inquiry(Session, row, user: dict, *, drafting: bool = True) -> None:
    """Yes/No offer, then an editable message the employee sends themselves.

    While a request is still being drafted (not yet confirmed), an "Ask the
    seller a question" button is always available too.
    """
    key = _inquiry_key(row)
    inq = st.session_state.get(key)
    if not inq:
        if drafting and st.button(
            "Ask the seller a question",
            key=f"inq_open_{row.id}",
            icon=":material/chat:",
            help="Message the seller on WhatsApp before you confirm your request.",
        ):
            draft = st.session_state.get("draft")
            product = None
            if draft is not None:
                product = (draft.attributes or {}).get("item") or draft.category
            st.session_state[key] = {"stage": "compose", "question": "", "product": product}
            st.rerun()
        return
    draft = st.session_state.get("draft")
    department = getattr(draft, "department", None) or None

    with st.container(border=True):
        if inq["stage"] == "offer":
            st.markdown("**Contact the seller for more info?**")
            c1, c2 = st.columns(2)
            if c1.button("Yes, write to the seller", key=f"inq_yes_{row.id}", type="primary"):
                inq["stage"] = "compose"
                st.rerun()
            if c2.button("No thanks", key=f"inq_no_{row.id}"):
                st.session_state.pop(key, None)
                _assistant(Session, row, "No problem. Carry on with your request whenever you're ready.")
                st.rerun()
            return

        phone = seller_inquiry.seller_phone()
        st.markdown(f"**Message to the seller** (WhatsApp {phone})")
        st.caption("Edit it as you like. Nothing is sent until you press Send.")
        text = st.text_area(
            "Your message",
            value=seller_inquiry.default_message(
                inq["question"], employee=user["display_name"],
                department=department, product=inq.get("product"),
            ),
            key=f"inq_text_{row.id}",
            height=170,
        )
        ready = seller_inquiry.contact_configured()
        if not ready:
            st.info("Seller contact isn't set up on this server "
                    "(GROK_WEBHOOK_URL / GROK_WEBHOOK_AUTHORIZATION).")
        if inq.get("error"):
            st.warning(inq["error"])
        unfilled = "[Type your question here]" in text
        if unfilled:
            st.caption("Replace [Type your question here] with your question, then press Send.")
        c1, c2 = st.columns(2)
        if c1.button("Send to seller", key=f"inq_send_{row.id}", type="primary",
                     disabled=not (ready and text.strip()) or unfilled):
            with st.spinner("Sending your message to the seller…"):
                result = seller_inquiry.send_inquiry(
                    text, employee=user["display_name"], department=department,
                    product=inq.get("product"), chat_ref=f"chat-{row.id}",
                )
            if result.sent:
                st.session_state.pop(key, None)
                st.session_state.pop(f"inq_text_{row.id}", None)
                quoted = "\n".join(f"> {line}" if line else ">" for line in text.strip().splitlines())
                _assistant(
                    Session, row,
                    f"Sent to the seller on WhatsApp ({phone}), reference **{result.order_id}**:\n\n"
                    f"{quoted}\n\nYou can keep refining your request meanwhile.",
                )
            else:
                hint = (" Check whether it arrived before sending again."
                        if result.status == "uncertain" else "")
                inq["error"] = f"Not sent: {result.detail}{hint}"
            st.rerun()
        if c2.button("Cancel", key=f"inq_cancel_{row.id}"):
            st.session_state.pop(key, None)
            st.session_state.pop(f"inq_text_{row.id}", None)
            st.rerun()


def render(engine, user: dict) -> None:
    st.title("Chatbot")
    st.caption(
        "Describe what you need — I'll confirm a structured request before the "
        "agent researches suppliers."
    )

    Session = session_factory(engine)
    with Session() as session:
        row = _load_or_create_session(session, user)

        # ---- session picker (left column) ---------------------------------
        left, right = st.columns([1, 3], gap="medium")
        with left:
            st.subheader("Sessions")
            st.caption(
                f"{chat_service.MAX_SESSIONS} max — oldest is replaced"
            )
            all_sessions = chat_service.list_sessions(session)
            for s in all_sessions:
                if st.button(
                    s.title[:24],
                    key=f"chat_s_{s.id}",
                    use_container_width=True,
                    type="primary" if s.id == row.id else "secondary",
                ):
                    st.session_state.chat_session_id = s.id
                    st.rerun()
            st.write("")
            if st.button(
                "New chat",
                icon=":material/add:",
                use_container_width=True,
                type="secondary",
            ):
                row = chat_service.create_session(session, user_id=user["id"])
                st.session_state.chat_session_id = row.id
                st.session_state.draft = None
                st.session_state.draft_for_session = row.id
                st.rerun()

        # ---- conversation (right column) ----------------------------------
        with right:
            messages = chat_service.list_messages(session, row.id)
            for msg in messages:
                with st.chat_message(msg.role):
                    st.markdown(msg.content)
            if not messages:
                with st.chat_message("assistant"):
                    st.markdown(GREETING)

            prompt = st.chat_input("Describe your purchase request…")

        # ---- processing happens between columns' render phases ------------
        if prompt:
            chat_service.add_message(session, row, role="user", content=prompt)
            if row.title == "New chat":
                row.title = prompt[:40]
                session.commit()
            with st.chat_message("user"):
                st.markdown(prompt)

            # A question about a product: answer briefly, offer to ask the seller,
            # and leave the request draft untouched.
            current = st.session_state.get("draft")
            pq = seller_inquiry.classify(
                prompt, _draft_markdown(current) if current is not None else ""
            )
            if pq is not None:
                _offer_seller_contact(session, row, prompt, pq)
                st.rerun()

            try:
                draft = _update_draft(prompt, st.session_state.draft)
            except Exception as exc:  # network / key / parse failure
                with st.chat_message("assistant"):
                    st.error(f"Sorry, I couldn't process that: {exc}")
                _assistant(session, row, f"Sorry, I couldn't process that: {exc}")
                st.stop()

            # A question about the form: answer it, keep the draft unchanged.
            if draft.answer:
                with st.chat_message("assistant"):
                    st.markdown(draft.answer)
                _assistant(session, row, draft.answer)
                st.stop()

            st.session_state.draft = draft
            chat_service.save_draft(
                session, row, draft.model_dump_json(exclude_none=True)
            )

            if draft.clarification_needed:
                fields = ", ".join(draft.unclear_fields) or "some details"
                msg = f"I need a bit more detail on: **{fields}**. Could you clarify?"
                with st.chat_message("assistant"):
                    st.markdown(msg)
                _assistant(session, row, msg)
                st.stop()

            try:
                draft_to_request(
                    draft,
                    request_id="chat",
                    requester=user["display_name"],
                    requester_department=user.get("department") or "",
                )
            except ClarificationNeeded as exc:
                fields = ", ".join(exc.missing_fields)
                msg = f"I still need: **{fields}**. Could you clarify?"
                with st.chat_message("assistant"):
                    st.markdown(msg)
                _assistant(session, row, msg)
                st.stop()

            parts = ["Here's what I understood:\n\n" + _draft_markdown(draft, user)]
            if draft.out_of_scope:
                parts.append(
                    "\n*(Note: I only source office equipment — ignoring: "
                    + ", ".join(draft.out_of_scope)
                    + ".)*"
                )
            summary = "\n".join(parts)
            with st.chat_message("assistant"):
                st.markdown(summary)
            _assistant(session, row, summary)

        # ---- optional: ask the seller about a product (before Confirm) -----
        _render_seller_inquiry(
            session, row, user,
            drafting=chat_service.linked_order(session, row) is None,
        )

        # ---- confirm / edit ------------------------------------------------
        st.session_state.setdefault("draft", None)
        linked = chat_service.linked_order(session, row)
        draft = st.session_state.draft

        if linked is not None:
            render_order_status(engine, linked.id)
        elif draft is not None:
            col1, col2 = st.columns(2)
            if col1.button("Confirm", type="primary"):
                try:
                    request = draft_to_request(
                        draft,
                        request_id="chat",
                        requester=user["display_name"],
                        requester_department=user.get("department") or "",
                    )
                except ClarificationNeeded as exc:
                    st.error(f"I still need: **{', '.join(exc.missing_fields)}**.")
                    st.stop()
                order = order_service.create_order(
                    session,
                    requester_id=user["id"],
                    department=request.department,
                    category=request.category,
                    quantity=request.quantity,
                    budget_minor=request.max_total_cost_minor,
                    deadline=request.deadline,
                    requirements={
                        "category": request.mandatory.category,
                        "attributes": request.mandatory.attributes,
                        "warranty_months_min": request.mandatory.warranty_months_min,
                    },
                    preferences=request.preferences,
                    source="chat",
                )
                order_service.submit_order(session, order)
                start_research_thread(engine, order.id)
                chat_service.link_order(session, row, order.id)
                st.session_state.draft = None
                chat_service.save_draft(session, row, "null")
                msg = (
                    f"Request confirmed — order **{order.order_no}** created. "
                    "The agent is researching suppliers now."
                )
                with st.chat_message("assistant"):
                    st.markdown(msg)
                _assistant(session, row, msg)
                st.rerun()
            if col2.button("Edit"):
                st.session_state.draft = None
                chat_service.save_draft(session, row, "null")
                msg = "Sure — what would you like to change?"
                with st.chat_message("assistant"):
                    st.markdown(msg)
                _assistant(session, row, msg)
                st.rerun()
