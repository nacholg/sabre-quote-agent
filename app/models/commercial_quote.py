from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class CommercialQuoteSegment(BaseModel):
    marketing_carrier: str
    flight_number: str
    departure_airport: str
    arrival_airport: str
    departure_at: datetime
    arrival_at: datetime


class CommercialQuoteFare(BaseModel):
    cabin: str
    brand_name: str | None = None
    brand_code: str | None = None
    currency: str
    price_per_passenger: Decimal
    total_price: Decimal | None = None
    baggage: str
    conditions: list[str] = Field(default_factory=list)
    fare_basis_codes: list[str] = Field(default_factory=list)
    last_ticket_date: str | None = None
    q1_amount: Decimal | None = None
    q1_currency: str | None = None


class CommercialQuoteOption(BaseModel):
    source_rank: int
    display_number: int
    segments: list[CommercialQuoteSegment]
    fares: list[CommercialQuoteFare]


class CommercialQuoteDocument(BaseModel):
    quote_id: str
    client_name: str | None = None
    client_reference: str | None = None
    notes: str | None = None
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None
    return_date: str | None = None
    passenger_count: int
    options: list[CommercialQuoteOption]
    disclaimer: str = "Tarifas sujetas a disponibilidad al momento de emisión."
