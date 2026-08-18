from datetime import datetime, timezone
from decimal import Decimal

from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services.quote_service import (
    _apply_excluded_carriers,
    _diversify_ranked_by_carrier,
    _matches_preferred_carriers,
)
from app.services.ranking import rank_itineraries


def _option(carrier: str, price: str, source_index: int) -> ItineraryOption:
    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal(price),
        total_price=Decimal(price),
    )
    segment = FlightSegment(
        marketing_carrier=carrier,
        operating_carrier=carrier,
        flight_number=str(source_index + 100),
        departure_airport="EZE",
        arrival_airport="MIA",
        departure_at=datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc),
        arrival_at=datetime(2026, 12, 20, 21, 0, tzinfo=timezone.utc),
    )
    return ItineraryOption(
        segments=[segment],
        fare=fare,
        fares_by_currency={"USD": fare},
        fare_options_by_currency={"USD": [fare]},
        source_index=source_index,
    )


def test_explicit_carrier_filter_is_strict():
    aa = _option("AA", "1000", 1)
    ar = _option("AR", "900", 2)
    assert _matches_preferred_carriers(aa, ["AA"]) is True
    assert _matches_preferred_carriers(ar, ["AA"]) is False


def test_multiple_allowed_carriers_are_supported():
    aa = _option("AA", "1000", 1)
    ar = _option("AR", "900", 2)
    assert _matches_preferred_carriers(aa, ["AA", "AR"]) is True
    assert _matches_preferred_carriers(ar, ["AA", "AR"]) is True


def test_excluded_carrier_filter():
    aa = _option("AA", "1000", 1)
    ar = _option("AR", "900", 2)
    assert _apply_excluded_carriers([aa, ar], ["AR"]) == [aa]


def test_unrestricted_display_prefers_carrier_diversity():
    options = [
        _option("LA", "700", 1),
        _option("LA", "710", 2),
        _option("LA", "720", 3),
        _option("AR", "800", 4),
        _option("AA", "900", 5),
    ]
    ranked = rank_itineraries(options, preferred_currency="USD")
    diversified = _diversify_ranked_by_carrier(ranked, 3)
    carriers = [
        item.option.segments[0].marketing_carrier
        for item in diversified
    ]
    assert carriers == ["LA", "AR", "AA"]
    assert [item.rank for item in diversified] == [1, 2, 3]
