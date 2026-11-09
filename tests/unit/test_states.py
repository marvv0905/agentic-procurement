import pytest

from procurement.states import (
    TERMINAL_STATES,
    StateTransitionError,
    WorkflowState,
    is_terminal,
    validate_transition,
)

HAPPY_PATH = [
    WorkflowState.DRAFT,
    WorkflowState.RESEARCHING,
    WorkflowState.EVALUATING,
    WorkflowState.PROPOSAL_READY,
    WorkflowState.AWAITING_APPROVAL,
    WorkflowState.REVALIDATING,
    WorkflowState.SUBMITTING,
    WorkflowState.SUBMITTED,
]


def test_happy_path_transitions_valid():
    for current, nxt in zip(HAPPY_PATH, HAPPY_PATH[1:]):
        validate_transition(current, nxt)  # should not raise


def test_illegal_transition_raises():
    with pytest.raises(StateTransitionError):
        validate_transition(WorkflowState.DRAFT, WorkflowState.SUBMITTED)


def test_terminal_states_have_no_outgoing():
    for state in TERMINAL_STATES:
        assert is_terminal(state)
        for candidate in WorkflowState:
            if candidate is not state:
                with pytest.raises(StateTransitionError):
                    validate_transition(state, candidate)


def test_need_clarification_branch():
    validate_transition(WorkflowState.DRAFT, WorkflowState.NEEDS_CLARIFICATION)
    validate_transition(WorkflowState.NEEDS_CLARIFICATION, WorkflowState.DRAFT)
