from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.itinerary import ItineraryOption
from app.models.commercial_quote import CommercialFare
from app.models.quote_request import (
    Cabin,
    FarePreference,
    PassengerSpec,
    QuoteSearchRequest,
    RequestProfile,
    SearchLeg,
    TimeConstraint,
    TripType,
    infer_trip_type,
)
from app.services.pricing_rules import PricingCurrency
from app.services.ranking import CommercialLabel, RankingMode


class QuoteSearchAPIRequest(BaseModel):
    environment: Literal["cert", "prod"] = "cert"

    origin: str | None = Field(default=None, min_length=3, max_length=3)
    destination: str | None = Field(default=None, min_length=3, max_length=3)
    departure_date: date | None = None
    return_date: date | None = None
    departure_time: time = time(12, 0)
    return_time: time = time(12, 0)

    trip_type: TripType | None = None
    legs: list[SearchLeg] = Field(default_factory=list)

    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    child_age: int = Field(default=6, ge=2, le=11)
    infants: int = Field(default=0, ge=0, le=9)
    passengers: list[PassengerSpec] = Field(default_factory=list)

    cabin: Cabin = Cabin.ECONOMY
    cabins: list[Cabin] = Field(default_factory=list)
    outbound_cabin: Cabin | None = None
    return_cabin: Cabin | None = None
    direct: bool = False
    max_stops: int = Field(default=1, ge=0, le=3)
    max_options: int = Field(default=5, ge=1, le=50)

    currency: PricingCurrency = PricingCurrency.AUTO
    carriers: list[str] = Field(default_factory=list)
    excluded_carriers: list[str] = Field(default_factory=list)
    fare_preference: FarePreference = FarePreference.AUTO
    sort: RankingMode = RankingMode.BALANCED
    request_profile: RequestProfile = RequestProfile.STANDARD
    business_companion: bool = True
    time_constraints: list[TimeConstraint] = Field(default_factory=list)
    persist: bool = True

    @model_validator(mode="after")
    def normalize_cabin_selection(self) -> "QuoteSearchAPIRequest":
        if self.cabins:
            ordered: list[Cabin] = []
            for cabin in self.cabins:
                if cabin not in ordered:
                    ordered.append(cabin)
            self.cabins = ordered
            self.cabin = ordered[0]
        return self

    @property
    def effective_cabins(self) -> list[Cabin]:
        return self.cabins or [self.cabin]

    @property
    def has_mixed_leg_cabins(self) -> bool:
        return bool(
            self.outbound_cabin
            and self.return_cabin
            and self.outbound_cabin != self.return_cabin
        )

    @model_validator(mode="after")
    def synchronize_passenger_compatibility_fields(self) -> "QuoteSearchAPIRequest":
        if self.passengers:
            from app.models.quote_request import PassengerKind
            self.adults = sum(
                p.quantity for p in self.passengers if p.type == PassengerKind.ADULT
            )
            self.children = sum(
                p.quantity for p in self.passengers if p.type == PassengerKind.CHILD
            )
            self.infants = sum(
                p.quantity for p in self.passengers if p.type == PassengerKind.INFANT
            )
            child_specs = [p for p in self.passengers if p.type == PassengerKind.CHILD]
            if len(child_specs) == 1 and child_specs[0].age is not None:
                self.child_age = child_specs[0].age
        return self

    @model_validator(mode="after")
    def validate_route(self) -> "QuoteSearchAPIRequest":
        if self.legs:
            return self
        missing = [
            name for name, value in (
                ("origin", self.origin),
                ("destination", self.destination),
                ("departure_date", self.departure_date),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                "Faltan campos obligatorios cuando no se usa legs: " + ", ".join(missing)
            )
        return self

    def to_search_request(self) -> QuoteSearchRequest:
        if self.legs:
            origin = self.legs[0].origin
            destination = self.legs[0].destination
            departure_date = self.legs[0].departure_date
            trip_type = self.trip_type or infer_trip_type(self.legs)
            return_date = None
        else:
            assert self.origin and self.destination and self.departure_date
            origin = self.origin
            destination = self.destination
            departure_date = self.departure_date
            trip_type = self.trip_type or (
                TripType.ROUND_TRIP if self.return_date else TripType.ONE_WAY
            )
            return_date = self.return_date

        return QuoteSearchRequest(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            departure_time=self.departure_time,
            return_time=self.return_time,
            trip_type=trip_type,
            legs=self.legs,
            adults=self.adults,
            children=self.children,
            child_age=self.child_age,
            infants=self.infants,
            passengers=self.passengers,
            cabin=self.cabin,
            max_stops=0 if self.direct else self.max_stops,
            max_options=self.max_options,
            currency=self.currency,
            preferred_carriers=self.carriers,
            excluded_carriers=self.excluded_carriers,
            request_profile=self.request_profile,
            fare_preference=self.fare_preference,
            time_constraints=self.time_constraints,
        )


class SabreSearchCall(BaseModel):
    currency: str
    cabin: str
    mode: Literal["primary", "carrier_fallback"] = "primary"
    preferred_carriers: list[str] = Field(default_factory=list)
    transaction_id: str | None = None
    itinerary_count: int = 0
    normalized_count: int = 0
    post_filter_count: int = 0
    no_availability: bool = False
    fallback_used: bool = False


class RankedOption(BaseModel):
    rank: int
    score: Decimal
    stops: int
    duration_minutes: int
    ranking_currency: str
    ranking_price: Decimal
    commercial_labels: list[CommercialLabel] = Field(default_factory=list)
    itinerary: ItineraryOption


class TimeMatchDiagnostics(BaseModel):
    status: Literal["not_requested", "exact", "fallback", "preferred"] = "not_requested"
    fallback_used: bool = False
    candidate_count: int = 0
    exact_match_count: int = 0
    preferred_match_count: int = 0
    selected_count: int = 0
    messages: list[str] = Field(default_factory=list)


class QuoteSearchAPIResponse(BaseModel):
    quote_id: str | None = None
    operation_id: str | None = Field(
        default=None,
        pattern=r"^[0-9A-F]{8}$",
    )
    environment: str
    effective_currencies: list[str]
    calls: list[SabreSearchCall]
    result_count: int
    available_option_count: int = 0
    options: list[RankedOption]
    candidate_options: list[RankedOption] = Field(
        default_factory=list,
        exclude=True,
        repr=False,
    )
    client_quote: str
    time_match: TimeMatchDiagnostics = Field(default_factory=TimeMatchDiagnostics)


class AgentQuoteRequest(BaseModel):
    text: str = Field(min_length=3)
    environment: Literal["cert", "prod"] = "cert"
    execute: bool = True
    max_options: int | None = Field(default=None, ge=1, le=50)


class AgentInterpretation(BaseModel):
    parser: str = "deterministic-v1"
    confidence: float = Field(ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    search_request: QuoteSearchAPIRequest


class AgentQuoteResponse(BaseModel):
    interpretation: AgentInterpretation
    quote: QuoteSearchAPIResponse | None = None


class QuoteModificationRequest(BaseModel):
    text: str = Field(min_length=3, max_length=1000)
    execute: bool = True


class QuoteChangeItem(BaseModel):
    field: str
    label: str
    before: str | int | bool | None = None
    after: str | int | bool | None = None


class QuoteModificationResponse(BaseModel):
    base_quote_id: str
    new_quote_id: str | None = None
    parser: str = "conversation-delta-v1"
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    changes: list[QuoteChangeItem] = Field(default_factory=list)
    search_request: QuoteSearchAPIRequest
    quote: QuoteSearchAPIResponse | None = None


class QuoteFareChoice(BaseModel):
    rank: int = Field(ge=1)
    fare_index: int = Field(ge=0)


class QuoteFareSelection(QuoteFareChoice):
    fare: CommercialFare


class StoredQuoteSummary(BaseModel):
    quote_id: str
    created_at: str
    updated_at: str
    status: str
    selected_ranks: list[int] = Field(default_factory=list)
    source: str
    client_name: str | None = None
    client_reference: str | None = None
    parent_quote_id: str | None = None
    sent_at: str | None = None
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None
    return_date: str | None = None
    passenger_count: int = 0
    result_count: int = 0


class StoredQuoteRecord(BaseModel):
    quote_id: str
    created_at: str
    updated_at: str
    status: str
    selected_ranks: list[int] = Field(default_factory=list)
    selected_fares: list[QuoteFareSelection] = Field(default_factory=list)
    source: str
    client_name: str | None = None
    client_reference: str | None = None
    notes: str | None = None
    sent_at: str | None = None
    parent_quote_id: str | None = None
    refreshed_quote_id: str | None = None
    agent_text: str | None = None
    interpretation: dict | None = None
    search_request: dict
    quote_response: dict


class QuoteSelectionRequest(BaseModel):
    ranks: list[int] = Field(min_length=1, max_length=10, examples=[[1]])
    fares: list[QuoteFareChoice] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_positive_ranks(self) -> "QuoteSelectionRequest":
        if any(rank < 1 for rank in self.ranks):
            raise ValueError("Todos los ranks deben ser mayores o iguales a 1.")
        if len(set(self.ranks)) != len(self.ranks):
            raise ValueError("No se pueden seleccionar ranks repetidos.")

        fare_ranks = [item.rank for item in self.fares]
        if len(set(fare_ranks)) != len(fare_ranks):
            raise ValueError(
                "No se pueden seleccionar dos tarifas para la misma opción."
            )

        rank_set = set(self.ranks)
        if any(rank not in rank_set for rank in fare_ranks):
            raise ValueError(
                "Las tarifas seleccionadas deben pertenecer a una opción "
                "incluida en ranks."
            )

        self.ranks = sorted(self.ranks)
        self.fares = sorted(self.fares, key=lambda item: item.rank)
        return self


class QuoteSelectionResponse(BaseModel):
    quote_id: str
    status: str
    selected_ranks: list[int]
    selected_count: int
    selected_fares: list[QuoteFareSelection] = Field(default_factory=list)


class QuoteRenderResponse(BaseModel):
    quote_id: str
    format: Literal["whatsapp", "email"]
    selected_ranks: list[int]
    content_type: str
    content: str


class QuoteArtifactCreate(BaseModel):
    artifact_type: Literal["whatsapp", "email", "rules", "reprice"]
    title: str = Field(min_length=1, max_length=200)
    selected_ranks: list[int] = Field(default_factory=list)
    content_type: str = Field(default="text/plain", max_length=100)
    content: str


class QuoteArtifactRecord(BaseModel):
    artifact_id: int
    quote_id: str
    artifact_type: Literal["whatsapp", "email", "rules", "reprice"]
    title: str
    selected_ranks: list[int] = Field(default_factory=list)
    content_type: str
    content: str
    created_at: str


class FareRuleDatum(BaseModel):
    status: Literal["included", "with_fee", "not_allowed", "allowed", "unknown"]
    source: Literal["brand_feature", "fare_flag", "baggage", "ticketing", "air_rules", "not_provided"]
    confidence: Literal["high", "medium", "unknown"]
    text: str


class FareRulePenalty(BaseModel):
    amount: Decimal
    currency: str
    text: str | None = None


class FareRuleConditionDetail(BaseModel):
    status: Literal["allowed", "not_allowed", "with_fee", "unknown"] = "unknown"
    amount: Decimal | None = None
    currency: str | None = None
    fare_difference_applies: bool | None = None
    source_text: str | None = None


class FareRuleStructuredDetails(BaseModel):
    changes_before_departure: FareRuleConditionDetail | None = None
    changes_after_departure: FareRuleConditionDetail | None = None
    cancellation_before_departure: FareRuleConditionDetail | None = None
    cancellation_after_departure: FareRuleConditionDetail | None = None
    no_show: FareRuleConditionDetail | None = None


class FareRuleCommercialSummary(BaseModel):
    baggage: str
    changes: str
    refunds: str
    no_show: str | None = None
    ticketing: str


class FareRuleFareAudit(BaseModel):
    cabin: str
    brand_name: str | None = None
    brand_code: str | None = None
    currency: str
    price_per_passenger: Decimal
    baggage: FareRuleDatum
    changes: FareRuleDatum
    refunds: FareRuleDatum
    ticketing: FareRuleDatum
    structured_details: FareRuleStructuredDetails | None = None
    commercial_summary: FareRuleCommercialSummary | None = None
    changes_penalty: FareRulePenalty | None = None
    refunds_penalty: FareRulePenalty | None = None
    change_fare_difference_applies: bool | None = None


class FareRuleOptionAudit(BaseModel):
    rank: int
    fares: list[FareRuleFareAudit]


class FareRuleAuditResponse(BaseModel):
    quote_id: str
    selected_only: bool
    options: list[FareRuleOptionAudit]
    requires_external_rule_lookup: bool
    external_rule_provider: Literal["air_rules"] = "air_rules"
    external_rule_lookup_status: Literal[
        "not_needed",
        "pending_authentication",
        "lookup_failed",
        "partial",
        "resolved",
    ] = "not_needed"


class QuoteWorkflowUpdate(BaseModel):
    client_name: str | None = Field(default=None, max_length=200)
    client_reference: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    status: Literal["active", "selected", "ready", "sent", "superseded"] | None = None


class QuoteWorkflowResponse(BaseModel):
    quote_id: str
    status: str
    client_name: str | None = None
    client_reference: str | None = None
    notes: str | None = None
    sent_at: str | None = None
    parent_quote_id: str | None = None
    refreshed_quote_id: str | None = None


class QuoteVersionItem(BaseModel):
    quote_id: str
    version: int = Field(ge=1)
    status: str
    source: str
    created_at: str
    updated_at: str
    selected_ranks: list[int] = Field(default_factory=list)
    sent_at: str | None = None
    is_current: bool = False
    is_latest: bool = False


class QuoteVersionHistory(BaseModel):
    quote_id: str
    root_quote_id: str
    latest_quote_id: str
    current_version: int = Field(ge=1)
    total_versions: int = Field(ge=1)
    is_latest: bool
    versions: list[QuoteVersionItem] = Field(min_length=1)


class FarePriceChange(BaseModel):
    cabin: str
    brand_name: str | None = None
    currency: str
    old_price: Decimal
    new_price: Decimal | None = None
    delta: Decimal | None = None
    status: Literal["same", "changed", "unavailable"]


class RefreshedOptionComparison(BaseModel):
    old_rank: int
    new_rank: int | None = None
    itinerary_status: Literal["same", "unavailable"]
    fare_changes: list[FarePriceChange] = Field(default_factory=list)


class QuoteRefreshResponse(BaseModel):
    original_quote_id: str
    refreshed_quote_id: str
    comparisons: list[RefreshedOptionComparison]
