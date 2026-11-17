"""PurchasePilot — Streamlit app shell.

Login gate + a sidebar navigation over the six pages. All persistent state
lives in SQLite; the agent blackbox runs in background threads writing order
status, so Streamlit reruns never restart research.
"""

from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="PurchasePilot",
    page_icon=":material/shopping_cart:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from procurement.ui.theme import apply_theme

apply_theme()


@st.cache_resource
def get_engine():
    """One engine per process; create tables and seed on first use."""
    from procurement.repository import create_engine_from_url, init_and_seed

    engine = create_engine_from_url()
    init_and_seed(engine)
    return engine


engine = get_engine()

if "user" not in st.session_state:
    st.session_state.user = None

from procurement import order_service
from procurement.repository import session_factory
from procurement.ui import (
    approvals,
    chat,
    history,
    login,
    suppliers_page,
    users_page,
    wizard,
)

if st.session_state.user is None:
    login.render(engine)
    st.stop()

user = st.session_state.user
is_manager = user["role"] == "manager"

# Approval count (managers only) — refreshed every rerun.
pending_count = 0
if is_manager:
    Session = session_factory(engine)
    with Session() as session:
        pending_count = len(order_service.pending_approvals(session))


def _chat_page():
    chat.render(engine, user)


def _wizard_page():
    wizard.render(engine, user)


def _history_page():
    history.render(engine, user)


def _approvals_page():
    approvals.render(engine, user)


def _suppliers_page():
    suppliers_page.render(engine, user)


def _users_page():
    users_page.render(engine, user)


def _seller_contact_page():
    from procurement.ui import seller_contact_page

    seller_contact_page.render(engine, user)


approvals_title = "Pending Approvals" + (f" ({pending_count})" if pending_count else "")

pages = [
    st.Page(_chat_page, title="Chatbot", icon=":material/forum:", default=True),
    st.Page(_wizard_page, title="Order Request", icon=":material/edit_note:"),
    st.Page(_history_page, title="All Orders", icon=":material/receipt_long:"),
]
if is_manager:
    pages.append(
        st.Page(
            _approvals_page, title=approvals_title, icon=":material/fact_check:"
        )
    )
    pages.append(
        st.Page(
            _seller_contact_page, title="Contact Sellers", icon=":material/chat:"
        )
    )
pages.append(st.Page(_suppliers_page, title="Suppliers", icon=":material/storefront:"))
if is_manager:
    pages.append(
        st.Page(_users_page, title="User Management", icon=":material/manage_accounts:")
    )

nav = st.navigation(pages)

# ---- sidebar identity ----------------------------------------------------------
with st.sidebar:
    st.markdown(
        f"**{user['display_name']}**  \n"
        f'<span class="pp-badge pp-badge--neutral">{user["role"].title()}</span>',
        unsafe_allow_html=True,
    )
    st.divider()
    if st.button("Log out", use_container_width=True):
        st.session_state.user = None
        st.session_state.pop("chat_session_id", None)
        st.session_state.pop("draft", None)
        st.session_state.pop("draft_for_session", None)
        # Wizard keys too — a stale draft id must never cross accounts.
        st.session_state.pop("wizard", None)
        st.session_state.pop("wizard_order_id", None)
        st.session_state.pop("wizard_submitted_order", None)
        st.rerun()

nav.run()