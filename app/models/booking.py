from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FlightSegment
from app.models.quote_request import PassengerSpec


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
