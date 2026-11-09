"""Pydantic schemas for PurchasePilot.

These are the typed contracts shared across the rules engine, controller,
approval gate, and tool stubs. They mirror the illustrative offer schema in the
project proposal (§9) and the core-table field lists (§8).

Money is always integer HKD minor units (see ``money``). Dates are timezone
aware where a wall-clock time matters; delivery dates are plain ``date`` values.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Currency(str, Enum):
    HKD = "HKD"


class ApprovalRole(str, Enum):
    REQUESTER = "requester"
    MANAGER = "manager"


class ValidationStatus(str, Enum):
    """Three-valued validation: a fact is PASS, FAIL, or UNKNOWN.

    UNKNOWN is deliberately distinct from FAIL; it means "not enough evidence to
    decide", never "assume it failed" or "assume it passed".
    """

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class RequestRequirements(BaseModel):
    """Mandatory attributes for the selected category (office monitors)."""

    model_config = ConfigDict(extra="forbid")

    screen_inches_min: int | None = None
    resolution: str | None = None
    connectors: list[str] = Field(default_factory=list)
    warranty_months_min: int | None = None


class PurchaseRequest(BaseModel):
    """A structured purchase request submitted by a requester."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    requester: str
    department: str
    category: str = "monitor"
    quantity: int = Field(gt=0)
    currency: Currency = Currency.HKD
    max_total_cost_minor: int = Field(ge=0)
    deadline: date
    mandatory: RequestRequirements = Field(default_factory=RequestRequirements)
    preferences: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class OfferEvidence(BaseModel):
    """Extracted supplier facts, before validation.

    Absent facts must be ``None`` with an explanation — never zero, empty string,
    or an invented default. ``unknown_fields`` records which facts could not be
    extracted and why.
    """

    model_config = ConfigDict(extra="forbid")

    offer_id: str
    supplier_id: str
    product_id: str
    currency: Currency = Currency.HKD
    unit_price_minor: int | None = None
    available_quantity: int | None = None
    shipping_minor: int | None = None
    screen_inches: int | None = None
    resolution: str | None = None
    connectors: list[str] | None = None
    warranty_months: int | None = None
    delivery_by: date | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    retrieved_at: datetime
    unknown_fields: dict[str, str] = Field(default_factory=dict)


class FieldValidation(BaseModel):
    """The outcome for a single mandatory requirement or commercial term."""

    field: str
    status: ValidationStatus
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class EligibilityResult(BaseModel):
    """Aggregate eligibility for a single offer."""

    offer_id: str
    status: ValidationStatus
    checks: list[FieldValidation] = Field(default_factory=list)
    verified_total_minor: int | None = None

    @property
    def eligible(self) -> bool:
        return self.status is ValidationStatus.PASS


class ComparisonResult(BaseModel):
    """Ranked eligible offers plus disclosed shortfalls."""

    request_id: str
    eligible_offers: list[str] = Field(default_factory=list)
    ranked_offers: list[str] = Field(default_factory=list)
    selected_offer_id: str | None = None
    quotation_shortfall: bool = False
    tradeoffs: list[str] = Field(default_factory=list)


class Proposal(BaseModel):
    """An immutable, versioned proposal awaiting human approval."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    request_id: str
    version: int
    selected_offer_id: str
    portal_payload: dict
    policy_version: str
    budget_snapshot_minor: int
    created_at: datetime
    hash: str


class ApprovalRecord(BaseModel):
    """A human approval bound to an exact proposal hash."""

    proposal_id: str
    proposal_hash: str
    reviewer: str
    role: ApprovalRole
    decision: str
    exceptions_approved: list[str] = Field(default_factory=list)
    decided_at: datetime


class RevalidationResult(BaseModel):
    """Outcome of re-checking material facts before submission."""

    proposal_id: str
    valid: bool
    changes: list[str] = Field(default_factory=list)


class SubmissionResult(BaseModel):
    """Outcome of submitting a requisition to the mock portal."""

    proposal_id: str
    request_key: str
    portal_record_id: str | None = None
    status: str
    verified: bool = False


class VerificationResult(BaseModel):
    """Post-submission verification of the portal record."""

    request_key: str
    portal_record_id: str | None = None
    fields_match: bool
    status: str
