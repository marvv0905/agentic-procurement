"""Workflow state machine.

States and transitions follow the project proposal §8. Transitions are
server-side operations validated here — never LLM-controlled labels.

The transition map matches the state diagram exactly. Two open design points
are deliberately left for later: (1) a retry path out of ``RESEARCH_BLOCKED``,
and (2) a global cancellation transition from every active state. Neither is
encoded yet; they can be added as explicit edges when decided.
"""

from __future__ import annotations

from enum import Enum


class WorkflowState(str, Enum):
    DRAFT = "DRAFT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    RESEARCHING = "RESEARCHING"
    EVALUATING = "EVALUATING"
    PROPOSAL_READY = "PROPOSAL_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REVALIDATING = "REVALIDATING"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"

    # Alternative outcomes / terminal states.
    NO_COMPLIANT_OFFER = "NO_COMPLIANT_OFFER"
    REJECTED = "REJECTED"
    APPROVAL_STALE = "APPROVAL_STALE"
    RESEARCH_BLOCKED = "RESEARCH_BLOCKED"
    SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: Allowed transitions: ``current -> {possible next states}``.
TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.DRAFT: frozenset(
        {WorkflowState.NEEDS_CLARIFICATION, WorkflowState.RESEARCHING}
    ),
    WorkflowState.NEEDS_CLARIFICATION: frozenset(
        {WorkflowState.DRAFT, WorkflowState.CANCELLED}
    ),
    WorkflowState.RESEARCHING: frozenset(
        {WorkflowState.EVALUATING, WorkflowState.RESEARCH_BLOCKED}
    ),
    WorkflowState.EVALUATING: frozenset(
        {WorkflowState.PROPOSAL_READY, WorkflowState.NO_COMPLIANT_OFFER}
    ),
    WorkflowState.PROPOSAL_READY: frozenset({WorkflowState.AWAITING_APPROVAL}),
    WorkflowState.AWAITING_APPROVAL: frozenset(
        {WorkflowState.REVALIDATING, WorkflowState.REJECTED}
    ),
    WorkflowState.REVALIDATING: frozenset(
        {
            WorkflowState.SUBMITTING,
            WorkflowState.PROPOSAL_READY,
            WorkflowState.APPROVAL_STALE,
        }
    ),
    WorkflowState.SUBMITTING: frozenset(
        {WorkflowState.SUBMITTED, WorkflowState.SUBMISSION_UNCERTAIN}
    ),
    WorkflowState.SUBMISSION_UNCERTAIN: frozenset({WorkflowState.SUBMITTING}),
    # Terminal states have no outgoing transitions.
    WorkflowState.SUBMITTED: frozenset(),
    WorkflowState.NO_COMPLIANT_OFFER: frozenset(),
    WorkflowState.REJECTED: frozenset(),
    WorkflowState.APPROVAL_STALE: frozenset(),
    WorkflowState.RESEARCH_BLOCKED: frozenset(),
    WorkflowState.FAILED: frozenset(),
    WorkflowState.CANCELLED: frozenset(),
}

TERMINAL_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.SUBMITTED,
        WorkflowState.NO_COMPLIANT_OFFER,
        WorkflowState.REJECTED,
        WorkflowState.APPROVAL_STALE,
        WorkflowState.RESEARCH_BLOCKED,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
    }
)


class StateTransitionError(ValueError):
    """Raised when an invalid workflow transition is attempted."""


def is_terminal(state: WorkflowState) -> bool:
    return state in TERMINAL_STATES


def validate_transition(current: WorkflowState, next_state: WorkflowState) -> None:
    """Raise :class:`StateTransitionError` if ``current -> next_state`` is illegal."""
    if next_state not in TRANSITIONS.get(current, frozenset()):
        raise StateTransitionError(
            f"illegal transition {current.value} -> {next_state.value}"
        )
