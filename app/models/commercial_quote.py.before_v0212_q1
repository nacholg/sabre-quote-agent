from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.itinerary import FlightSegment
from app.models.quote_request import SearchLeg


class CommercialPassengerPrice(BaseModel):
    passenger_type: str
    quantity: int = 1
    age: int | None = None
    currency: str
    unit_price: Decimal
    total_price: Decimal


class CommercialFareRules(BaseModel):
    baggage: str | None = None
    changes: str | None = None
    refunds: str | None = None
    no_show: str | None = None


class CommercialFare(BaseModel):
    cabin: str
    currency: str
    brand_name: str | None = None
    brand_code: str | None = None
    price_per_passenger: Decimal
    total_price: Decimal | None = None
    passenger_prices: list[CommercialPassengerPrice] = Field(default_factory=list)
    fare_basis_codes: list[str] = Field(default_factory=list)
    validating_carrier: str | None = None
    q1_amount: Decimal | None = None
    q1_currency: str | None = None
    rules: CommercialFareRules = Field(default_factory=CommercialFareRules)


class CommercialOption(BaseModel):
    rank: int
    score: Decimal | None = None
    stops: int | None = None
    duration_minutes: int | None = None
    commercial_labels: list[str] = Field(default_factory=list)
    segments: list[FlightSegment]
    fares: list[CommercialFare]


class CommercialQuote(BaseModel):
    quote_id: str
    environment: str
    trip_type: str | None = None
    legs: list[SearchLeg] = Field(default_factory=list)
    client_name: str | None = None
    client_reference: str | None = None
    options: list[CommercialOption] = Field(default_factory=list)
