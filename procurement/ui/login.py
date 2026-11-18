"""Login page: username + password against the seeded users table."""

from __future__ import annotations

import streamlit as st

from .. import auth
from ..repository import session_factory


def render(engine) -> None:
    st.title("Agentic Procurement")
    st.caption('\n\n\n\n\n\n')

    left, form_col, right = st.columns([1, 2, 1], gap="large")
    with form_col:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")

    if submitted:
        Session = session_factory(engine)
        with Session() as session:
            user = auth.login(session, username, password)
        if user is None:
            st.error("Username or password is incorrect. Check both and try again.")
        else:
            st.session_state.user = {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "role": user.role,
                "department": user.department or "",
            }
            st.rerun()