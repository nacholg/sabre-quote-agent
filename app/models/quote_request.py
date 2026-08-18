from datetime import date, time
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.pricing_rules import PricingCurrency


class Cabin(StrEnum):
    ECONOMY = "ECONOMY"
    PREMIUM_ECONOMY = "PREMIUM_ECONOMY"
    BUSINESS = "BUSINESS"
    FIRST = "FIRST"


class RequestProfile(StrEnum):
    OFFICIAL = "official"
    STANDARD = "standard"


class TripType(StrEnum):
    ONE_WAY = "one_way"
    ROUND_TRIP = "round_trip"
    OPEN_JAW = "open_jaw"
    CIRCLE_TRIP = "circle_trip"
    MULTI_CITY = "multi_city"


class FarePreference(StrEnum):
    LOWEST = "lowest"
    BAGGAGE = "baggage"
    BRANDED = "branded"
    REFUNDABLE = "refundable"
    AUTO = "auto"


class PassengerKind(StrEnum):
    ADULT = "ADT"
    CHILD = "CHILD"
    INFANT = "INF"


class PassengerSpec(BaseModel):
    type: PassengerKind
    quantity: int = Field(default=1, ge=1, le=9)
    age: int | None = Field(default=None, ge=0, le=120)

    @model_validator(mode="after")
    def validate_age(self) -> "PassengerSpec":
        if self.type == PassengerKind.CHILD:
            if self.age is None:
                raise ValueError("Los pasajeros CHILD requieren edad.")
            if not 2 <= self.age <= 11:
                raise ValueError("CHILD aplica únicamente para edades de 2 a 11 años.")
        elif self.type == PassengerKind.INFANT:
            if self.age is not None and self.age >= 2:
                raise ValueError("INF aplica únicamente a menores de 2 años.")
        elif self.type == PassengerKind.ADULT:
            if self.age is not None and self.age < 12:
                raise ValueError("ADT con edad informada requiere 12 años o más.")
        return self

    @property
    def sabre_code(self) -> str:
        if self.type == PassengerKind.CHILD:
            assert self.age is not None
            return f"C{self.age:02d}"
        return self.type.value


class SearchLeg(BaseModel):
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: date
    departure_time: time = time(12, 0)

    @field_validator("origin", "destination")
    @classmethod
    def uppercase_codes(cls, value: str) -> str:
        return value.upper().strip()


class QuoteSearchRequest(BaseModel):
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: date
    return_date: date | None = None
    departure_time: time = time(12, 0)
    return_time: time = time(12, 0)
    trip_type: TripType = TripType.ONE_WAY
    legs: list[SearchLeg] = Field(default_factory=list)
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    child_age: int = Field(default=6, ge=2, le=11)
    infants: int = Field(default=0, ge=0, le=9)
    passengers: list[PassengerSpec] = Field(default_factory=list)
    cabin: Cabin = Cabin.ECONOMY
    max_stops: int = Field(default=1, ge=0, le=3)
    max_options: int = Field(default=5, ge=1, le=50)
    currency: PricingCurrency = PricingCurrency.AUTO
    preferred_carriers: list[str] = Field(default_factory=list)
    excluded_carriers: list[str] = Field(default_factory=list)
    request_profile: RequestProfile = RequestProfile.STANDARD
    request_baggage: bool = True
    fare_preference: FarePreference = FarePreference.AUTO

    @field_validator("origin", "destination")
    @classmethod
    def uppercase_codes(cls, value: str) -> str:
        return value.upper().strip()

    @field_validator("preferred_carriers", "excluded_carriers")
    @classmethod
    def uppercase_carriers(cls, values: list[str]) -> list[str]:
        cleaned = [value.upper().strip() for value in values]
        if any(len(value) != 2 for value in cleaned):
            raise ValueError("Cada aerolínea preferida debe tener código IATA de 2 caracteres")
        return cleaned

    @model_validator(mode="after")
    def validate_dates_and_passengers(self) -> "QuoteSearchRequest":
        if self.return_date and self.return_date < self.departure_date:
            raise ValueError("return_date no puede ser anterior a departure_date")
        if self.legs:
            dates = [leg.departure_date for leg in self.legs]
            if dates != sorted(dates):
                raise ValueError("Las fechas de --leg deben estar en orden cronológico")

        # Backward compatibility: older callers may still send adults/children/child_age/infants.
        if not self.passengers:
            generated: list[PassengerSpec] = [
                PassengerSpec(type=PassengerKind.ADULT, quantity=self.adults)
            ]
            if self.children:
                generated.append(
                    PassengerSpec(
                        type=PassengerKind.CHILD,
                        age=self.child_age,
                        quantity=self.children,
                    )
                )
            if self.infants:
                generated.append(
                    PassengerSpec(type=PassengerKind.INFANT, quantity=self.infants)
                )
            self.passengers = generated

        adult_count = sum(
            p.quantity for p in self.passengers if p.type == PassengerKind.ADULT
        )
        infant_count = sum(
            p.quantity for p in self.passengers if p.type == PassengerKind.INFANT
        )
        if adult_count < 1:
            raise ValueError("La búsqueda requiere al menos un pasajero ADT.")
        if infant_count > adult_count:
            raise ValueError("No puede haber más infantes sin asiento que adultos.")

        # Keep legacy counters synchronized for old rendering/scripts.
        self.adults = adult_count
        self.children = sum(
            p.quantity for p in self.passengers if p.type == PassengerKind.CHILD
        )
        child_specs = [p for p in self.passengers if p.type == PassengerKind.CHILD]
        if len(child_specs) == 1 and child_specs[0].age is not None:
            self.child_age = child_specs[0].age
        self.infants = infant_count
        return self

    @property
    def seats_requested(self) -> int:
        return sum(
            p.quantity
            for p in self.passengers
            if p.type in {PassengerKind.ADULT, PassengerKind.CHILD}
        )

    def sabre_passenger_types(self) -> list[dict[str, int | str]]:
        grouped: dict[str, int] = {}
        for passenger in self.passengers:
            grouped[passenger.sabre_code] = (
                grouped.get(passenger.sabre_code, 0) + passenger.quantity
            )
        return [
            {"Code": code, "Quantity": quantity}
            for code, quantity in grouped.items()
        ]

    @model_validator(mode="after")
    def validate_carrier_filters(self) -> "QuoteSearchRequest":
        overlap = set(self.preferred_carriers) & set(self.excluded_carriers)
        if overlap:
            raise ValueError(
                "Una aerolínea no puede estar incluida y excluida a la vez: "
                + ", ".join(sorted(overlap))
            )
        return self

    def effective_legs(self) -> list[SearchLeg]:
        if self.legs:
            return self.legs
        result = [
            SearchLeg(
                origin=self.origin,
                destination=self.destination,
                departure_date=self.departure_date,
                departure_time=self.departure_time,
            )
        ]
        if self.return_date:
            result.append(
                SearchLeg(
                    origin=self.destination,
                    destination=self.origin,
                    departure_date=self.return_date,
                    departure_time=self.return_time,
                )
            )
        return result
