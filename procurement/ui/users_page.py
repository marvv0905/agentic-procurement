"""Page 5 — User Management (manager-only).

Add users, assign roles, deactivate/reactivate. Deactivate is used instead of
delete so FK history (orders, decisions) is preserved.
"""

from __future__ import annotations

import streamlit as st

from .. import auth
from ..repository import User, session_factory
from .common import require_manager


def render(engine, user: dict) -> None:
    require_manager(user)
    st.title("User Management")
    st.caption(
        "Two demo roles: **employees** and **managers** create order requests; "
        "only managers approve them. Users are deactivated, never deleted."
    )

    # ---- add user -------------------------------------------------------------
    with st.expander("Add a user"):
        with st.form("add_user_form"):
            username = st.text_input("Username")
            display_name = st.text_input("Display name")
            role = st.selectbox("Role", auth.ROLES)
            department = st.selectbox("Department", auth.DEPARTMENTS)
            password = st.text_input("Initial password", type="password")
            submitted = st.form_submit_button("Create user", type="primary")
        if submitted:
            Session = session_factory(engine)
            with Session() as session:
                try:
                    if not password:
                        raise ValueError("an initial password is required")
                    created = auth.create_user(
                        session,
                        username=username,
                        display_name=display_name,
                        role=role,
                        password=password,
                        department=department,
                    )
                except ValueError as exc:
                    st.error(f"Could not create user: {exc}")
                else:
                    st.success(f"Created {created.username} ({created.role}).")
                    st.rerun()

    # ---- user list --------------------------------------------------------------
    Session = session_factory(engine)
    with Session() as session:
        users = list(session.query(User).order_by(User.created_at.desc()))

    rows = [
        {
            "Username": u.username,
            "Display name": u.display_name,
            "Role": u.role.title(),
            "Department": u.department or "—",
            "Status": "Active" if u.active else "Inactive",
            "Created": u.created_at.strftime("%Y-%m-%d"),
        }
        for u in users
    ]
    st.table(rows)

    # ---- per-user management ------------------------------------------------------
    st.subheader("Manage a user")
    picked = st.selectbox(
        "Select a user", [u.username for u in users], key="manage_user_pick"
    )
    target = next(u for u in users if u.username == picked)
    self_target = target.id == user["id"]

    col1, col2 = st.columns(2)
    with col1:
        new_role = st.selectbox(
            "Role",
            auth.ROLES,
            index=auth.ROLES.index(target.role) if target.role in auth.ROLES else 0,
        )
        if st.button("Save role", disabled=(new_role == target.role)):
            Session = session_factory(engine)
            with Session() as session:
                row = session.get(User, target.id)
                row.role = new_role
                session.commit()
            st.success(f"{target.username} is now a {new_role}.")
            st.rerun()

        new_password = st.text_input(
            "Reset password (leave blank to keep)", type="password"
        )
        if st.button("Reset password", disabled=not new_password):
            Session = session_factory(engine)
            with Session() as session:
                row = session.get(User, target.id)
                row.password_hash = auth.hash_password(new_password)
                session.commit()
            st.success(f"Password reset for {target.username}.")
            st.rerun()

    with col2:
        new_department = st.selectbox(
            "Department",
            auth.DEPARTMENTS,
            index=(
                auth.DEPARTMENTS.index(target.department)
                if target.department in auth.DEPARTMENTS
                else 0
            ),
        )
        if st.button("Save department", disabled=(new_department == target.department)):
            Session = session_factory(engine)
            with Session() as session:
                row = session.get(User, target.id)
                row.department = new_department
                session.commit()
            st.success(f"{target.username}'s department is now {new_department}.")
            st.rerun()

        st.markdown(
            f"- **Status:** {'Active' if target.active else 'Inactive'}\n"
            f"- **Created:** {target.created_at.strftime('%Y-%m-%d')}"
        )
        if st.button(
            "Deactivate" if target.active else "Reactivate",
            disabled=self_target,
            help=(
                "You cannot deactivate your own account."
                if self_target
                else None
            ),
        ):
            Session = session_factory(engine)
            with Session() as session:
                row = session.get(User, target.id)
                row.active = not row.active
                session.commit()
            st.rerun()