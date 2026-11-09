"""Mock requisition portal tool stubs (§9).

The portal tool accepts a validated approval token bound to a proposal hash and
must reject calls without one. The submission implementation loads the stored
approved payload rather than asking the LLM to reconstruct it.
"""

from __future__ import annotations

from ..schemas import (
    ApprovalRecord,
    Proposal,
    SubmissionResult,
    VerificationResult,
)


class MissingApprovalError(RuntimeError):
    """Raised when a submission is attempted without a valid approval."""


def _require_approval(approval: ApprovalRecord | None, proposal: Proposal) -> ApprovalRecord:
    if approval is None:
        raise MissingApprovalError("no approval record provided for submission")
    from ..approvals import approval_matches_proposal

    if not approval_matches_proposal(approval, proposal):
        raise MissingApprovalError("approval does not match proposal hash")
    return approval


def submit_requisition(
    proposal: Proposal,
    approval: ApprovalRecord | None,
) -> SubmissionResult:
    """Populate and submit the exact approved payload to the mock portal."""
    _require_approval(approval, proposal)
    raise NotImplementedError("portal submission is not implemented yet (Phase 4).")


def verify_requisition(request_key: str) -> VerificationResult:
    """Verify the resulting portal record and its fields."""
    raise NotImplementedError("portal verification is not implemented yet (Phase 4).")
