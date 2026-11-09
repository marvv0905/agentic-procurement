"""Integration test: controller happy path and phase gating."""

import pytest

from procurement.controller import (
    PhaseGateError,
    WorkerConflictError,
    WorkflowController,
)
from procurement.states import WorkflowState


def _run_through_happy_path(controller: WorkflowController) -> None:
    path = [
        WorkflowState.RESEARCHING,
        WorkflowState.EVALUATING,
        WorkflowState.PROPOSAL_READY,
        WorkflowState.AWAITING_APPROVAL,
        WorkflowState.REVALIDATING,
        WorkflowState.SUBMITTING,
        WorkflowState.SUBMITTED,
    ]
    for state in path:
        controller.transition_to(state)


def test_phase_gating_blocks_portal_during_research():
    ctrl = WorkflowController(run_id="r1", request_id="REQ_1")
    ctrl.register_tool("submit_requisition", lambda *a, **k: None)
    with pytest.raises(PhaseGateError):
        ctrl.call_tool("submit_requisition")


def test_research_tool_allowed_in_researching():
    ctrl = WorkflowController(run_id="r1", request_id="REQ_1")
    ctrl.transition_to(WorkflowState.RESEARCHING)
    ctrl.register_tool("research_supplier", lambda *a, **k: "ok")
    assert ctrl.call_tool("research_supplier") == "ok"


def test_happy_path_reaches_submitted():
    ctrl = WorkflowController(run_id="r1", request_id="REQ_1")
    _run_through_happy_path(ctrl)
    assert ctrl.state is WorkflowState.SUBMITTED
    assert any(e.action == "transition" for e in ctrl.audit)


def test_single_active_worker():
    ctrl = WorkflowController(run_id="r1", request_id="REQ_1")
    ctrl.claim_worker("w1")
    with pytest.raises(WorkerConflictError):
        ctrl.claim_worker("w2")
