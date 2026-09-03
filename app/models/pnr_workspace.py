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


class PnrTicketing(BaseModel):
    ticket_type: str | None = None
    ticketing_text: str | None = None
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
    read_error_code: str | None = None
    read_error_message: str | None = None
