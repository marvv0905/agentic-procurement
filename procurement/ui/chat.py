"""Page 0 — Chatbot: persistent LLM sessions that turn into orders.

Messages and the running ``RequestDraft`` are persisted per chat session, so
conversations survive reruns and refreshes. Confirming a draft creates an
order and hands it to the agent blackbox. The demo keeps 5 sessions globally —
the oldest is overwritten when a new one is created.

Layout contract: the conversation column's message loop is the ONLY place a
chat message is ever rendered. When a prompt arrives it is persisted and a
``pending_prompt`` flag triggers one rerun; the next run shows the user's
message, then computes and persists the assistant reply and reruns again.
No transient second render path exists, so the input (pinned to the viewport
bottom by ``st.chat_input`` at mainline) always sits below the conversation it
feeds, and the draft actions always sit in the same column as the summary they
confirm.
"""

from __future__ import annotations

import asyncio
import logging

import streamlit as st

from .. import chat_service, order_service, seller_inquiry
from ..catalog import CATEGORIES, OTHER_CATEGORY
from ..llm.adapter import ConfigurableLLM
from ..parsing import ClarificationNeeded, draft_to_request
from ..repository import session_factory
from ..research import start_research_thread
from ..schemas import RequestDraft
from .common import render_order_status

logger = logging.getLogger(__name__)

GREETING = (
    "Hi! I source office equipment and "
    "I can search live supplier sites for anything else you describe!"
)

#: Session-state key holding a submitted prompt that still needs processing.
_PENDING_KEY = "pending_prompt"


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
    """Answer a product question briefly and offer to contact the seller.

    The reply is a persisted assistant message; the conversation column's
    loop renders it after the rerun.
    """
    parts = [pq.reply] if pq.reply else []
    parts.append(
        "The seller can confirm the details. **Would you like to contact the seller "
        "for more info?** Your request draft stays as it is either way."
    )
    _assistant(Session, row, "\n\n".join(parts))
    st.session_state[_inquiry_key(row)] = {
        "stage": "offer", "question": prompt, "product": pq.product,
    }
    st.rerun()


def _render_seller_inquiry(Session, row, user: dict, *, drafting: bool = True) -> None:
    """Yes/No offer, then an editable message the employee sends themselves.

    While a request draft exists (and is not yet confirmed), an "Ask the
    seller a question" button is available too.
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
    # Department comes from the requester's account: since prompt v8 the draft
    # never carries one (it is assigned in draft_to_request at confirm time).
    department = user.get("department") or None

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


def _process_prompt(session, row, user: dict, prompt: str) -> None:
    """Turn one user message into the next assistant reply.

    Every branch persists exactly one assistant message (via ``_assistant``)
    and reruns, so the conversation column's loop — the only render path —
    shows it. This function never falls through: the ``st.rerun()`` tail is
    unreachable but kept as a backstop.
    """
    current = st.session_state.get("draft")

    # A question about a product: answer briefly, offer to ask the seller,
    # and leave the request draft untouched.
    pq = seller_inquiry.classify(
        prompt,
        _draft_markdown(current, user) if current is not None else "",
    )
    if pq is not None:
        _offer_seller_contact(session, row, prompt, pq)  # persists + reruns

    try:
        draft = _update_draft(prompt, current)
    except Exception:  # network / key / parse failure
        logger.warning("chat draft update failed", exc_info=True)
        _assistant(
            session,
            row,
            "Sorry — I couldn't process that. Please try rephrasing "
            "your request.",
        )
        st.rerun()

    # A question about the form: answer it, keep the draft unchanged.
    if draft.answer:
        _assistant(session, row, draft.answer)
        st.rerun()

    st.session_state.draft = draft
    chat_service.save_draft(
        session, row, draft.model_dump_json(exclude_none=True)
    )

    if draft.clarification_needed:
        fields = ", ".join(draft.unclear_fields) or "some details"
        _assistant(
            session, row,
            f"I need a bit more detail on: **{fields}**. Could you clarify?",
        )
        st.rerun()

    try:
        draft_to_request(
            draft,
            request_id="chat",
            requester=user["display_name"],
            requester_department=user.get("department") or "",
        )
    except ClarificationNeeded as exc:
        fields = ", ".join(exc.missing_fields)
        _assistant(
            session, row,
            f"I still need: **{fields}**. Could you clarify?",
        )
        st.rerun()

    parts = ["Here's what I understood:\n\n" + _draft_markdown(draft, user)]
    if draft.out_of_scope:
        parts.append(
            "\n*(Note: I only source office equipment — ignoring: "
            + ", ".join(draft.out_of_scope)
            + ".)*"
        )
    _assistant(session, row, "\n".join(parts))
    st.rerun()


def _confirm_draft(session, row, user: dict, engine) -> None:
    """Turn the working draft into an order; the confirmation is a persisted
    assistant message rendered by the conversation loop after the rerun."""
    draft = st.session_state.get("draft")
    if draft is None:
        return
    try:
        request = draft_to_request(
            draft,
            request_id="chat",
            requester=user["display_name"],
            requester_department=user.get("department") or "",
        )
    except ClarificationNeeded as exc:
        st.error(
            f"I still need: **{', '.join(exc.missing_fields)}**. "
            "Could you clarify?"
        )
        return
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
    _assistant(
        session,
        row,
        f"Request confirmed — order **{order.order_no}** created. "
        "The agent is researching suppliers now.",
    )
    st.rerun()


def render(engine, user: dict) -> None:
    st.title("Chatbot")

    Session = session_factory(engine)
    with Session() as session:
        row = _load_or_create_session(session, user)

        # A prompt submitted on the previous run: its user message is already
        # persisted (the loop below shows it); this run produces the reply.
        pending = st.session_state.get(_PENDING_KEY)

        # ---- session picker (left column) ---------------------------------
        left, right = st.columns([1, 3], gap="medium")
        with left:
            st.subheader("Sessions")
            st.caption(f"{chat_service.MAX_SESSIONS} Maximum. Overwrites oldest session!")
            all_sessions = chat_service.list_sessions(session)
            for s in all_sessions:
                active = s.id == row.id
                if st.button(
                    s.title[:24],
                    key=f"chat_s_{s.id}",
                    use_container_width=True,
                    type="primary" if active else "secondary",
                    # Second cue beyond fill color for the active session.
                    icon=":material/chat_bubble:" if active else None,
                    # The full title is one hover (or focus) away.
                    help=s.title,
                ):
                    st.session_state.chat_session_id = s.id
                    st.session_state.pop(_PENDING_KEY, None)
                    st.rerun()
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
                st.session_state.pop(_PENDING_KEY, None)
                st.rerun()

        # ---- conversation + request flow (right column) --------------------
        # The message loop is the single render path for chat messages.
        with right:
            st.subheader("Conversation")
            messages = chat_service.list_messages(session, row.id)
            for msg in messages:
                with st.chat_message(msg.role):
                    st.markdown(msg.content)
            if not messages:
                with st.chat_message("assistant"):
                    st.markdown(GREETING)

            if pending is not None:
                # Feedback while the model reads the message; never persisted,
                # so it lives exactly as long as the processing run.
                with st.chat_message("assistant"):
                    st.markdown("_Reading your request…_")
                st.session_state.pop(_PENDING_KEY, None)
                _process_prompt(session, row, user, pending)
                st.rerun()  # pragma: no cover - _process_prompt always reruns

            # ---- ask the seller about a product (before confirming) --------
            # The proactive button only makes sense once there is a draft to
            # anchor it to; the contextual offer (classify → product question)
            # still works in an empty chat.
            _render_seller_inquiry(
                session, row, user,
                drafting=(
                    st.session_state.get("draft") is not None
                    and chat_service.linked_order(session, row) is None
                ),
            )

            # ---- confirm / edit ---------------------------------------------
            st.session_state.setdefault("draft", None)
            linked = chat_service.linked_order(session, row)
            draft = st.session_state.draft

            if linked is not None:
                render_order_status(engine, linked.id)
            elif draft is not None:
                col1, col2 = st.columns(2)
                if col1.button("Confirm", type="primary"):
                    _confirm_draft(session, row, user, engine)
                if col2.button("Edit"):
                    st.session_state.draft = None
                    chat_service.save_draft(session, row, "null")
                    _assistant(
                        session, row, "Sure — what would you like to change?"
                    )
                    st.rerun()

        # ---- chat input ----------------------------------------------------
        # Mainline, outside every column: Streamlit pins st.chat_input to
        # the bottom of the viewport, so the box always sits below the
        # conversation it feeds, independent of page length.
        prompt = st.chat_input("Describe your purchase request")
        if prompt:
            chat_service.add_message(session, row, role="user", content=prompt)
            if row.title == "New chat":
                row.title = prompt[:40]
                session.commit()
            st.session_state[_PENDING_KEY] = prompt
            st.rerun()