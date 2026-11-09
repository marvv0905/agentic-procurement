"""Workflow controller and phase-gated tool dispatch.

The controller is the only component that advances workflow state and the only
entry point through which tools are invoked. Tool exposure is gated by phase:
research tools are available only during research/evaluation; portal submission
is available only during submission. This is enforced here, not by the LLM.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .states import WorkflowState, validate_transition

#: Tools permitted in each phase. Research can never access portal submission.
TOOLS_BY_PHASE: dict[WorkflowState, frozenset[str]] = {
    WorkflowState.RESEARCHING: frozenset({"research_supplier"}),
    WorkflowState.EVALUATING: frozenset({"validate_offer", "compare_offers"}),
    WorkflowState.PROPOSAL_READY: frozenset({"create_proposal"}),
    WorkflowState.AWAITING_APPROVAL: frozenset({"approve_proposal"}),
    WorkflowState.REVALIDATING: frozenset({"revalidate_proposal"}),
    WorkflowState.SUBMITTING: frozenset({"submit_requisition", "verify_requisition"}),
}


@dataclass
class AuditEvent:
    actor: str
    action: str
    result: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class PhaseGateError(RuntimeError):
    """Raised when a tool is invoked outside its permitted phase."""


class WorkerConflictError(RuntimeError):
    """Raised when a second worker tries to claim an already-active run."""


class CancelledError(RuntimeError):
    """Raised when a run is cancelled between actions."""


class WorkflowController:
    """Owns a single run's state, worker claim, and audit log."""

    def __init__(self, run_id: str, request_id: str) -> None:
        self.run_id = run_id
        self.request_id = request_id
        self.state = WorkflowState.DRAFT
        self._active_worker: str | None = None
        self._cancelled = False
        self._tools: dict[str, Callable[..., Any]] = {}
        self.audit: list[AuditEvent] = []

    # --- worker claim -------------------------------------------------------
    def claim_worker(self, worker_id: str) -> None:
        if self._active_worker is not None and self._active_worker != worker_id:
            raise WorkerConflictError(
                f"run {self.run_id} already has active worker {self._active_worker}"
            )
        self._active_worker = worker_id

    def release_worker(self, worker_id: str) -> None:
        if self._active_worker == worker_id:
            self._active_worker = None

    # --- cancellation -------------------------------------------------------
    def cancel(self, actor: str = "system") -> None:
        self._cancelled = True
        self.record(actor, "cancel", "cancelled")

    def _ensure_not_cancelled(self) -> None:
        if self._cancelled:
            raise CancelledError(f"run {self.run_id} was cancelled")

    # --- state transitions --------------------------------------------------
    def transition_to(self, next_state: WorkflowState, actor: str = "system") -> None:
        validate_transition(self.state, next_state)
        self.state = next_state
        self.record(actor, "transition", next_state.value)

    # --- tool registry + gating ---------------------------------------------
    def register_tool(self, name: str, fn: Callable[..., Any]) -> None:
        self._tools[name] = fn

    def available_tools(self) -> frozenset[str]:
        return TOOLS_BY_PHASE.get(self.state, frozenset())

    def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self._ensure_not_cancelled()
        allowed = self.available_tools()
        if name not in allowed:
            raise PhaseGateError(
                f"tool {name!r} not permitted in state {self.state.value}; "
                f"allowed: {sorted(allowed)}"
            )
        fn = self._tools.get(name)
        if fn is None:
            raise NotImplementedError(f"tool {name!r} is not registered")
        result = fn(*args, **kwargs)
        self.record("worker", name, "ok")
        return result

    # --- audit --------------------------------------------------------------
    def record(self, actor: str, action: str, result: str, metadata: dict[str, Any] | None = None) -> None:
        self.audit.append(
            AuditEvent(actor=actor, action=action, result=result, metadata=metadata or {})
        )
