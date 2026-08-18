from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class FlightSegment(BaseModel):
    marketing_carrier: str
    operating_carrier: str | None = None
    flight_number: str
    departure_airport: str
    arrival_airport: str
    departure_country: str | None = None
    arrival_country: str | None = None
    departure_at: datetime
    arrival_at: datetime
    booking_class: str | None = None
    cabin_code: str | None = None
    seats_available: int | None = None


class TaxDetail(BaseModel):
    code: str
    amount: Decimal
    currency: str
    description: str | None = None
    station: str | None = None
    country: str | None = None


class BrandFeature(BaseModel):
    application: str
    commercial_name: str
    service_type: str | None = None
    service_group: str | None = None
    sub_code: str | None = None

    @property
    def status(self) -> str:
        return {
            "F": "included",
            "N": "not_offered",
            "C": "chargeable",
            "D": "displayed_not_offered",
        }.get(self.application, "unknown")


class BrandedComponent(BaseModel):
    component_ref: int | None = None
    begin_airport: str | None = None
    end_airport: str | None = None
    fare_basis_code: str | None = None
    governing_carrier: str | None = None
    vendor_code: str | None = None
    tariff: str | None = None
    rule_number: str | None = None
    fare_amount: Decimal | None = None
    fare_currency: str | None = None
    brand_code: str | None = None
    brand_name: str | None = None
    program_code: str | None = None
    features: list[BrandFeature] = Field(default_factory=list)


class FareOption(BaseModel):
    cabin: str
    currency: str
    price_per_passenger: Decimal
    total_price: Decimal | None = None
    total_tax: Decimal | None = None
    base_fare_amount: Decimal | None = None
    base_fare_currency: str | None = None
    equivalent_amount: Decimal | None = None
    equivalent_currency: str | None = None
    exchange_rate: Decimal | None = None
    pricing_modifier: str | None = None
    taxes: list[TaxDetail] = Field(default_factory=list)
    q1_amount: Decimal | None = None
    q1_currency: str | None = None
    fare_basis_codes: list[str] = Field(default_factory=list)
    validating_carrier: str | None = None
    non_refundable: bool | None = None
    last_ticket_date: str | None = None
    baggage_pieces: int | None = None
    baggage: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    brand_code: str | None = None
    brand_name: str | None = None
    branded_components: list[BrandedComponent] = Field(default_factory=list)
    brand_features: list[BrandFeature] = Field(default_factory=list)


class ItineraryOption(BaseModel):
    segments: list[FlightSegment]
    # Primary/lowest fare retained for backwards compatibility and ranking.
    fare: FareOption
    fares_by_currency: dict[str, FareOption] = Field(default_factory=dict)
    # All available price points, including branded upsells.
    fare_options_by_currency: dict[str, list[FareOption]] = Field(default_factory=dict)
    source_index: int | None = None

    @property
    def is_domestic_argentina(self) -> bool:
        return bool(self.segments) and all(
            segment.departure_country == "AR" and segment.arrival_country == "AR"
            for segment in self.segments
        )
