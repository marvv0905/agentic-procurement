"""PurchasePilot — Streamlit app.

The request-capture step is a stateful LLM chatbot: the user describes their
need in natural language, the LLM merges it into a running ``RequestDraft``,
the app shows a confirmation block, and on confirm a deterministic
``PurchaseRequest`` is handed to the workflow controller.

All workflow state lives in ``st.session_state`` (backed by the controller), so
Streamlit reruns cannot restart research or repeat writes.
"""

from __future__ import annotations

import asyncio

import streamlit as st
from dotenv import load_dotenv

from procurement.catalog import CATEGORIES
from procurement.controller import WorkflowController
from procurement.llm.adapter import ConfigurableLLM
from procurement.parsing import ClarificationNeeded, draft_to_request
from procurement.schemas import RequestDraft
from procurement.states import WorkflowState

load_dotenv()

st.set_page_config(page_title="PurchasePilot", layout="wide")
st.title("PurchasePilot")
st.caption("Describe what you need — I'll confirm a structured request before research.")


def _init_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "controller" not in st.session_state:
        st.session_state.controller = WorkflowController(
            run_id="chat-run", request_id="REQ_001"
        )
    if "draft" not in st.session_state:
        st.session_state.draft: RequestDraft | None = None
    if "request" not in st.session_state:
        st.session_state.request = None
    if "confirmed" not in st.session_state:
        st.session_state.confirmed = False


def _update_draft(text: str, current: RequestDraft | None) -> RequestDraft:
    async def _run() -> RequestDraft:
        llm = ConfigurableLLM()
        return await llm.update_draft(text, current)

    return asyncio.run(_run())


def _draft_markdown(d: RequestDraft) -> str:
    lines = [
        f"- **Category:** {d.category or '—'}",
        f"- **Quantity:** {d.quantity or '—'}",
    ]
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
    lines.append(f"- **Deadline:** {d.deadline or '—'}")
    lines.append(f"- **Department:** {d.department or '—'}")
    if d.preferences:
        lines.append(f"- **Preferences:** {d.preferences}")
    return "\n".join(lines)


_init_state()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if not st.session_state.messages:
    with st.chat_message("assistant"):
        st.markdown(
            "Hi! I source office equipment — **monitors, keyboards, and chairs**. "
            "Describe what you need, e.g.:\n\n"
            "> 6 monitors, 27 inch, 2560x1440, HDMI and DisplayPort, 3-year "
            "warranty, budget HK$8,000, deliver by 2026-11-20, for the "
            "engineering team."
        )

prompt = st.chat_input("Describe your purchase request…")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        draft = _update_draft(prompt, st.session_state.draft)
    except Exception as exc:  # network / key / parse failure
        msg = f"Sorry, I couldn't process that: {exc}"
        st.session_state.messages.append({"role": "assistant", "content": msg})
        with st.chat_message("assistant"):
            st.error(msg)
        st.stop()

    # Form question (not request detail): answer it, keep the draft unchanged.
    if draft.answer:
        st.session_state.messages.append({"role": "assistant", "content": draft.answer})
        with st.chat_message("assistant"):
            st.markdown(draft.answer)
        st.stop()

    st.session_state.draft = draft

    if draft.clarification_needed:
        fields = ", ".join(draft.unclear_fields) or "some details"
        msg = f"I need a bit more detail on: **{fields}**. Could you clarify?"
        st.session_state.messages.append({"role": "assistant", "content": msg})
        with st.chat_message("assistant"):
            st.markdown(msg)
        st.stop()

    try:
        request = draft_to_request(
            draft, request_id="REQ_001", requester="chat-user"
        )
    except ClarificationNeeded as exc:
        fields = ", ".join(exc.missing_fields)
        msg = f"I still need: **{fields}**. Could you clarify?"
        st.session_state.messages.append({"role": "assistant", "content": msg})
        with st.chat_message("assistant"):
            st.markdown(msg)
        st.stop()

    st.session_state.request = request

    parts = ["Here's what I understood:\n\n" + _draft_markdown(draft)]
    if draft.out_of_scope:
        parts.append(
            "\n*(Note: I only source office equipment — ignoring: "
            + ", ".join(draft.out_of_scope)
            + ".)*"
        )
    summary = "\n".join(parts)
    st.session_state.messages.append({"role": "assistant", "content": summary})
    with st.chat_message("assistant"):
        st.markdown(summary)

if st.session_state.draft is not None and not st.session_state.confirmed:
    col1, col2 = st.columns(2)
    if col1.button("Confirm", type="primary"):
        st.session_state.confirmed = True
        st.session_state.controller.transition_to(WorkflowState.RESEARCHING)
    if col2.button("Edit"):
        st.session_state.draft = None
        st.session_state.request = None
        msg = "Sure — what would you like to change?"
        st.session_state.messages.append({"role": "assistant", "content": msg})
        st.rerun()

if st.session_state.confirmed:
    st.success(
        f"Request confirmed — workflow advanced to "
        f"**{st.session_state.controller.state.value}**."
    )
    st.caption("(Research / comparison / approval screens come in later phases.)")
