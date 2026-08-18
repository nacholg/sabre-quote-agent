from datetime import datetime, timezone
from decimal import Decimal

from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.models.quote_request import Cabin
from app.services.quote_service import (
    _fare_matches_requested_cabin,
    _filter_itineraries_to_cabin,
)


def _fare(
    cabin: str,
    codes: list[str],
    brand: str,
    price: str,
) -> FareOption:
    return FareOption(
        cabin=cabin,
        cabin_codes=codes,
        currency="USD",
        price_per_passenger=Decimal(price),
        total_price=Decimal(price),
        brand_name=brand,
    )


def _option(fares: list[FareOption]) -> ItineraryOption:
    segment = FlightSegment(
        marketing_carrier="AA",
        operating_carrier="AA",
        flight_number="900",
        departure_airport="EZE",
        arrival_airport="MIA",
        departure_at=datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc),
        arrival_at=datetime(2026, 12, 20, 21, 0, tzinfo=timezone.utc),
    )
    primary = fares[0]
    return ItineraryOption(
        segments=[segment],
        fare=primary,
        fares_by_currency={"USD": primary},
        fare_options_by_currency={"USD": fares},
        source_index=1,
    )


def test_brand_name_does_not_define_cabin():
    economy_plus = _fare(
        "economy",
        ["Y"],
        "ECONOMY PLUS",
        "900",
    )
    assert _fare_matches_requested_cabin(
        economy_plus,
        Cabin.ECONOMY,
    ) is True


def test_business_brand_is_rejected_from_economy_when_structured_cabin_is_c():
    misleading = _fare(
        "business",
        ["C"],
        "ECONOMY BUSINESS SPECIAL",
        "1500",
    )
    assert _fare_matches_requested_cabin(
        misleading,
        Cabin.ECONOMY,
    ) is False


def test_mixed_component_cabins_are_not_classified_as_single_cabin():
    mixed = _fare(
        "economy",
        ["Y", "C"],
        "MIXED CABIN",
        "1200",
    )
    assert _fare_matches_requested_cabin(
        mixed,
        Cabin.ECONOMY,
    ) is False
    assert _fare_matches_requested_cabin(
        mixed,
        Cabin.BUSINESS,
    ) is False


def test_itinerary_filter_keeps_only_requested_cabin_and_reselects_primary():
    business = _fare(
        "business",
        ["C"],
        "BUSINESS STANDARD",
        "1800",
    )
    economy = _fare(
        "economy",
        ["Y"],
        "ECONOMY PLUS",
        "950",
    )
    option = _option([business, economy])

    filtered = _filter_itineraries_to_cabin(
        [option],
        Cabin.ECONOMY,
    )

    assert len(filtered) == 1
    result = filtered[0]
    assert result.fare.brand_name == "ECONOMY PLUS"
    assert result.fare.cabin_codes == ["Y"]
    assert [
        fare.brand_name
        for fare in result.fare_options_by_currency["USD"]
    ] == ["ECONOMY PLUS"]


def test_itinerary_is_dropped_when_requested_cabin_has_no_fares():
    business = _fare(
        "business",
        ["C"],
        "BUSINESS STANDARD",
        "1800",
    )
    option = _option([business])

    filtered = _filter_itineraries_to_cabin(
        [option],
        Cabin.ECONOMY,
    )

    assert filtered == []
