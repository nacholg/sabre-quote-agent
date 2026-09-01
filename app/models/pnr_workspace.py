from __future__ import annotations

from decimal import Decimal
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
