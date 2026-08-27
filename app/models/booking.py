from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FlightSegment
from app.models.quote_request import PassengerKind, PassengerSpec, SearchLeg


class BookingStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    REVALIDATION_REQUIRED = "revalidation_required"
    REQUIRES_AGENT_ACTION = "requires_agent_action"
    READY_TO_CREATE_PNR = "ready_to_create_pnr"
    ABANDONED = "abandoned"
    # Reserved for the future Create PNR release. v0.31 never enters this state.
    PNR_CREATED = "pnr_created"


class RevalidationStatus(StrEnum):
    NOT_RUN = "not_run"
    MATCHED = "matched"
    PRICE_CHANGED = "price_changed"
    FARE_CHANGED = "fare_changed"
    ITINERARY_CHANGED = "itinerary_changed"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    STALE = "stale"


class BookingOfferSource(StrEnum):
    INITIAL = "initial"
    REVALIDATION = "revalidation"


class BookingOfferSnapshot(BaseModel):
    """Immutable canonical product snapshot owned by Booking.

    The browser never supplies this payload. It is resolved server-side from the
    persisted quote, exact itinerary and exact fare selection. Shopping
    alternatives are deliberately excluded from this snapshot.
    """

    source_quote_id: str
    rank: int = Field(ge=1)
    fare_index: int = Field(ge=0)
    segments: list[FlightSegment] = Field(min_length=1)
    fare: CommercialFare
    passenger_mix: list[PassengerSpec] = Field(default_factory=list)
    # Added in v0.31.4 so Revalidate can rebuild exact O&D grouping without
    # consulting mutable Shopping state. Empty keeps historical Bookings valid.
    legs: list[SearchLeg] = Field(default_factory=list)


class BookingOfferRevision(BaseModel):
    offer_revision_id: int
    booking_id: str
    revision_number: int = Field(ge=1)
    source: BookingOfferSource
    snapshot: BookingOfferSnapshot
    created_at: str
    accepted_at: str | None = None


class BookingCreateRequest(BaseModel):
    rank: int = Field(ge=1)
    client_request_id: UUID


class BookingRecord(BaseModel):
    booking_id: str
    source_quote_id: str
    selected_rank: int = Field(ge=1)
    environment: Literal["cert", "prod"]
    status: BookingStatus
    revalidation_status: RevalidationStatus = RevalidationStatus.NOT_RUN
    accepted_offer_revision_id: int | None = None
    revision: int = Field(ge=1)
    client_request_id: str
    created_at: str
    updated_at: str
    abandoned_at: str | None = None
    accepted_offer_revision: BookingOfferRevision | None = None


class BookingPassengerIdentityUpdate(BaseModel):
    """Editable identity fields for one fixed passenger slot."""

    slot_index: int = Field(ge=1)
    given_name: str | None = Field(default=None, max_length=60)
    middle_name: str | None = Field(default=None, max_length=60)
    surname: str | None = Field(default=None, max_length=60)
    date_of_birth: date | None = None
    gender: Literal["M", "F", "X"] | None = None
    associated_adult_slot_index: int | None = Field(default=None, ge=1)


class BookingPassengersUpdateRequest(BaseModel):
    revision: int = Field(ge=1)
    passengers: list[BookingPassengerIdentityUpdate] = Field(min_length=1)


class BookingPassengerRecord(BaseModel):
    slot_index: int = Field(ge=1)
    passenger_type: PassengerKind
    quoted_age: int | None = None
    given_name: str | None = None
    middle_name: str | None = None
    surname: str | None = None
    date_of_birth: date | None = None
    gender: Literal["M", "F", "X"] | None = None
    associated_adult_slot_index: int | None = None
    complete: bool = False


class BookingPassengersResponse(BaseModel):
    booking_id: str
    booking_revision: int = Field(ge=1)
    complete: bool
    passengers: list[BookingPassengerRecord]


class BookingPreferredChannel(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    WHATSAPP = "whatsapp"


class BookingContactUpdateRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    phone_country_code: str | None = Field(default=None, max_length=8)
    phone_number: str | None = Field(default=None, max_length=32)
    preferred_channel: BookingPreferredChannel | None = None


class BookingContactRecord(BaseModel):
    booking_id: str
    booking_revision: int = Field(ge=1)
    name: str | None = None
    email: str | None = None
    phone_country_code: str | None = None
    phone_number: str | None = None
    preferred_channel: BookingPreferredChannel | None = None
    complete: bool = False


class BookingReviewResponse(BaseModel):
    booking_id: str
    booking_revision: int = Field(ge=1)
    status: BookingStatus
    revalidation_status: RevalidationStatus
    ready_for_review: bool
    passengers_complete: bool
    contact_complete: bool
    offer_revision: BookingOfferRevision
    passengers: list[BookingPassengerRecord]
    contact: BookingContactRecord


class BookingRevalidationRequest(BaseModel):
    revision: int = Field(ge=1)


class BookingRevalidationResponse(BaseModel):
    booking_id: str
    booking_revision: int = Field(ge=1)
    status: BookingStatus
    revalidation_status: RevalidationStatus
    revalidation_id: int | None = None
    checked_at: str | None = None
    provider: str | None = None
    provider_reference: str | None = None
    source_offer_revision_id: int | None = None
    candidate_offer_revision_id: int | None = None
    diff: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    stale_at: str | None = None
