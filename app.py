"""PurchasePilot — Streamlit shell.

This is a thin UI that delegates all workflow logic to ``procurement.controller``.
State lives in the controller / SQLite, never in Streamlit, so a rerun cannot
restart research or repeat writes (proposal §8).
"""

from __future__ import annotations

import streamlit as st

from procurement.controller import WorkflowController
from procurement.states import WorkflowState

st.set_page_config(page_title="PurchasePilot", layout="wide")

st.title("PurchasePilot")
st.caption("Browser-assisted procurement agent — student prototype")

# Placeholder: the request form and workflow screens arrive in later phases.
st.subheader("New purchase request")

with st.form("request_form"):
    department = st.text_input("Department")
    category = st.selectbox("Category", ["monitor"])
    quantity = st.number_input("Quantity", min_value=1, value=1, step=1)
    max_total_hkd = st.number_input("Maximum total cost (HKD)", min_value=0.0, value=5000.0)
    submitted = st.form_submit_button("Submit request")

if submitted:
    st.info(
        "Request capture is not wired yet. The deterministic foundation "
        "(schemas, rules, state machine, seed) is in place; the request form "
        "flows through the controller in a later phase."
    )

# Placeholder: show the current workflow state of a hypothetical run.
st.divider()
st.subheader("Workflow state (skeleton)")
controller = WorkflowController(run_id="demo-run", request_id="demo-request")
st.write(f"Initial state: **{controller.state.value}**")
st.write("Allowed tools per phase:", sorted(controller.available_tools()))
