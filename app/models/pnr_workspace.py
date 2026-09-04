from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PnrPassenger(BaseModel):
    name_number: str
    rph: str | None = None
    passenger_type: str | None = None
    given_name: str | None = None
    surname: str | None = None
    with_infant: bool | None = None
    emails: list[str] = Field(default_factory=list)


class PnrContact(BaseModel):
    kind: Literal["phone", "email"]
    value: str
    name_number: str | None = None
    usage_type: str | None = None
    location_code: str | None = None
    comment: str | None = None


class PnrSegment(BaseModel):
    segment_number: str | None = None
    rph: str | None = None
    marketing_carrier: str | None = None
    operating_carrier: str | None = None
    flight_number: str | None = None
    origin: str | None = None
    destination: str | None = None
    departure_at: str | None = None
    arrival_at: str | None = None
    booking_class: str | None = None
    status: str | None = None
    number_in_party: int | None = Field(default=None, ge=0)
    airline_locator: str | None = None
    e_ticket: bool | None = None


class PnrPriceQuote(BaseModel):
    record_number: str | None = None
    status: str | None = None
    stored_at: str | None = None
    validating_carrier: str | None = None
    passenger_type: str | None = None
    passenger_quantity: int | None = Field(default=None, ge=0)
    passenger_name_numbers: list[str] = Field(default_factory=list)
    base_fare_amount: Decimal | None = None
    base_fare_currency: str | None = None
    equivalent_fare_amount: Decimal | None = None
    equivalent_fare_currency: str | None = None
    per_passenger_tax_amount: Decimal | None = None
    per_passenger_total_amount: Decimal | None = None
    total_amount: Decimal | None = None
    total_currency: str | None = None
    fare_basis: str | None = None
    fare_basis_codes: list[str] = Field(default_factory=list)
    segment_booking_classes: list[str] = Field(default_factory=list)
    purchase_deadline_raw: str | None = None
    itinerary_changed: bool | None = None


class PnrPricingSelectionStatus(StrEnum):
    SELECTED = "selected"
    MISSING = "missing"
    NO_ACTIVE = "no_active"


class PnrPricingSelection(BaseModel):
    status: PnrPricingSelectionStatus
    candidates: list[PnrPriceQuote] = Field(default_factory=list)
    total_quote_count: int = Field(default=0, ge=0)
    candidate_quote_count: int = Field(default=0, ge=0)
    excluded_quote_count: int = Field(default=0, ge=0)
    candidate_record_numbers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrPricingCoverageStatus(StrEnum):
    EXACT = "exact"
    UNKNOWN = "unknown"
    INCOMPLETE = "incomplete"
    CONFLICT = "conflict"


class PnrPricingPassengerBinding(BaseModel):
    name_number: str
    passenger_type: str | None = None
    candidate_record_numbers: list[str] = Field(default_factory=list)


class PnrPricingCoverage(BaseModel):
    status: PnrPricingCoverageStatus
    passenger_count: int = Field(default=0, ge=0)
    covered_passenger_count: int = Field(default=0, ge=0)
    bindings: list[PnrPricingPassengerBinding] = Field(default_factory=list)
    uncovered_name_numbers: list[str] = Field(default_factory=list)
    duplicate_name_numbers: list[str] = Field(default_factory=list)
    unknown_name_numbers: list[str] = Field(default_factory=list)
    type_mismatch_name_numbers: list[str] = Field(default_factory=list)
    quantity_mismatch_record_numbers: list[str] = Field(default_factory=list)
    unassociated_record_numbers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrTicketCandidateStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class PnrTicketCandidatePassenger(BaseModel):
    name_number: str
    passenger_type: str
    price_quote_record_number: str


class PnrTicketCandidate(BaseModel):
    status: PnrTicketCandidateStatus
    confirmation_id: str
    validating_carrier: str | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    price_quote_record_numbers: list[str] = Field(default_factory=list)
    passengers: list[PnrTicketCandidatePassenger] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrPreIssueReadinessStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class PnrPreIssueReadiness(BaseModel):
    status: PnrPreIssueReadinessStatus
    confirmation_id: str
    retrieved_at: str | None = None
    fresh_remote_read: bool = False
    blockers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrTicketingConstraintStatus(StrEnum):
    STRUCTURED_DEADLINE = "structured_deadline"
    ADVISORY_WITHOUT_DEADLINE = "advisory_without_deadline"
    NO_STRUCTURED_CONSTRAINT = "no_structured_constraint"
    UNVERIFIED_DEADLINE = "unverified_deadline"


class PnrTicketingConstraint(BaseModel):
    status: PnrTicketingConstraintStatus
    advisory_present: bool = False
    advisory_code: str | None = None
    advisory_status: str | None = None
    advisory_airline_code: str | None = None
    deadline_at: str | None = None
    deadline_interpretable: bool = False
    requires_deadline_lookup: bool = True
    message: str | None = None


class PnrPurchaseDeadlineStatus(StrEnum):
    RESOLVED = "resolved"
    EXPIRED = "expired"
    UNRESOLVED = "unresolved"


class PnrPurchaseDeadline(BaseModel):
    status: PnrPurchaseDeadlineStatus
    timezone: str = "America/Argentina/Buenos_Aires"
    purchase_deadline_at: str | None = None
    operational_deadline_at: str | None = None
    policy_cap_at: str | None = None
    policy_capped: bool = False
    source_record_numbers: list[str] = Field(default_factory=list)
    raw_values: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrSameBrandRequoteStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    FOUND = "found"
    SAME_BRAND_UNAVAILABLE = "same_brand_unavailable"
    EXACT_ITINERARY_UNAVAILABLE = "exact_itinerary_unavailable"
    BLOCKED = "blocked"


class PnrSameBrandRequoteResponse(BaseModel):
    booking_id: str
    confirmation_id: str
    status: PnrSameBrandRequoteStatus
    read_only: bool = True
    trigger_reasons: list[str] = Field(default_factory=list)
    source_brand_code: str | None = None
    source_brand_name: str | None = None
    source_currency: str | None = None
    source_total: Decimal | None = None
    candidate_brand_code: str | None = None
    candidate_brand_name: str | None = None
    candidate_currency: str | None = None
    candidate_total: Decimal | None = None
    price_difference: Decimal | None = None
    candidate_fare_basis_codes: list[str] = Field(default_factory=list)
    candidate_last_ticket_date: str | None = None
    provider: str | None = None
    provider_reference: str | None = None
    blockers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrAutomaticSameBrandRefreshStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    BLOCKED = "blocked"
    UPDATED = "updated"
    FAILED_SAFE = "failed_safe"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class PnrAutomaticSameBrandRefreshResponse(BaseModel):
    booking_id: str
    confirmation_id: str | None = None
    status: PnrAutomaticSameBrandRefreshStatus
    brand_code: str | None = None
    source_total: Decimal | None = None
    candidate_total: Decimal | None = None
    current_total: Decimal | None = None
    price_difference: Decimal | None = None
    pricing_authority_id: int | None = None
    sabre_mutation_performed: bool = False
    blockers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrPricingAuthority(BaseModel):
    pricing_authority_id: int = Field(ge=1)
    booking_id: str
    confirmation_id: str
    price_quote_record_numbers: list[str] = Field(default_factory=list)
    brand_code: str
    brand_name: str | None = None
    original_total: Decimal
    current_total: Decimal
    currency: str
    price_difference: Decimal
    validating_carrier: str | None = None
    fare_basis_codes: list[str] = Field(default_factory=list)
    purchase_deadline_raw: str | None = None
    provider: str
    verified_at: str


class PnrFinalPreIssueGateStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class PnrFinalPreIssueGate(BaseModel):
    status: PnrFinalPreIssueGateStatus
    confirmation_id: str
    evaluated_at: str
    ticketing_constraint_status: PnrTicketingConstraintStatus | None = None
    purchase_deadline_status: PnrPurchaseDeadlineStatus | None = None
    purchase_deadline_at: str | None = None
    operational_deadline_at: str | None = None
    deadline_at: str | None = None
    deadline_expired: bool | None = None
    blockers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrTicketing(BaseModel):
    ticket_type: str | None = None
    ticketing_text: str | None = None
    arrangement_raw: str | None = None
    arrangement_type: str | None = None
    arrangement_rph: str | None = None
    advisory_present: bool = False
    advisory_code: str | None = None
    advisory_status: str | None = None
    advisory_airline_code: str | None = None
    deadline_at: str | None = None


class PnrSpecialService(BaseModel):
    code: str
    status: str | None = None
    airline_code: str | None = None
    service_type: str | None = None
    name_numbers: list[str] = Field(default_factory=list)
    segment_numbers: list[str] = Field(default_factory=list)


class PnrSnapshot(BaseModel):
    confirmation_id: str
    application_status: str
    passengers: list[PnrPassenger] = Field(default_factory=list)
    segments: list[PnrSegment] = Field(default_factory=list)
    contacts: list[PnrContact] = Field(default_factory=list)
    price_quotes: list[PnrPriceQuote] = Field(default_factory=list)
    ticketing: PnrTicketing = Field(default_factory=PnrTicketing)
    special_services: list[PnrSpecialService] = Field(default_factory=list)


class PnrSecureFlightDocsStatus(StrEnum):
    COMPLETE = "complete"
    MISSING = "missing"
    UNVERIFIED = "unverified"


class PnrSecureFlightDocsCoverage(BaseModel):
    status: PnrSecureFlightDocsStatus
    passenger_count: int = Field(default=0, ge=0)
    covered_name_numbers: list[str] = Field(default_factory=list)
    missing_name_numbers: list[str] = Field(default_factory=list)
    unverified_name_numbers: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    message: str | None = None


class PnrCheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


class PnrWorkspaceStatus(StrEnum):
    SYNCING = "syncing"
    VERIFIED = "verified"
    NEEDS_ATTENTION = "needs_attention"
    READY_FOR_TICKETING = "ready_for_ticketing"
    READ_ERROR = "read_error"


class PnrNextActionCode(StrEnum):
    ISSUE_TICKET = "issue_ticket"
    STORE_OR_VERIFY_PRICING = "store_or_verify_pricing"
    REVIEW_ITINERARY = "review_itinerary"
    REVIEW_PASSENGERS = "review_passengers"
    REVIEW_CONTACT = "review_contact"
    REVIEW_PRICING = "review_pricing"
    REPRICE_REQUIRED = "reprice_required"


class PnrAssessmentCheck(BaseModel):
    code: str
    label: str
    status: PnrCheckStatus
    blocking: bool = False
    expected: str | None = None
    actual: str | None = None
    message: str | None = None


class PnrAssessment(BaseModel):
    status: PnrWorkspaceStatus
    checks: list[PnrAssessmentCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PnrNextAction(BaseModel):
    code: PnrNextActionCode
    label: str


class PnrAssessmentResult(BaseModel):
    assessment: PnrAssessment
    next_action: PnrNextAction
    pricing_selection: PnrPricingSelection
    pricing_coverage: PnrPricingCoverage
    ticket_candidate: PnrTicketCandidate
    pricing_authority: PnrPricingAuthority | None = None
    pricing_authority_current: bool | None = None
    secure_flight_docs: PnrSecureFlightDocsCoverage | None = None


class PnrWorkspaceSnapshotRecord(BaseModel):
    booking_id: str
    confirmation_id: str
    provider: str
    environment: Literal["cert", "prod"]
    retrieved_at: str
    snapshot: PnrSnapshot


class PnrWorkspaceResponse(BaseModel):
    booking_id: str
    confirmation_id: str
    provider: str
    environment: Literal["cert", "prod"]
    status: PnrWorkspaceStatus
    retrieved_at: str | None = None
    stale: bool = False
    snapshot: PnrSnapshot | None = None
    assessment: PnrAssessment | None = None
    next_action: PnrNextAction | None = None
    pricing_selection: PnrPricingSelection | None = None
    pricing_coverage: PnrPricingCoverage | None = None
    ticket_candidate: PnrTicketCandidate | None = None
    pricing_authority: PnrPricingAuthority | None = None
    pricing_authority_current: bool | None = None
    secure_flight_docs: PnrSecureFlightDocsCoverage | None = None
    pre_issue_readiness: PnrPreIssueReadiness | None = None
    ticketing_constraint: PnrTicketingConstraint | None = None
    purchase_deadline: PnrPurchaseDeadline | None = None
    final_pre_issue_gate: PnrFinalPreIssueGate | None = None
    read_error_code: str | None = None
    read_error_message: str | None = None
