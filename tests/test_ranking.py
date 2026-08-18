from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services.ranking import rank_itineraries


def make_option(price: str, stops: int, duration_hours: int, source: int) -> ItineraryOption:
    start = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
    segments = []
    current = start
    for i in range(stops + 1):
        end = start + timedelta(hours=duration_hours) if i == stops else current + timedelta(hours=2)
        segments.append(
            FlightSegment(
                marketing_carrier="XX",
                flight_number=str(100 + i),
                departure_airport="AAA" if i == 0 else f"X{i}",
                arrival_airport="BBB" if i == stops else f"X{i+1}",
                departure_at=current,
                arrival_at=end,
            )
        )
        current = end + timedelta(hours=1)
    fare = FareOption(cabin="economy", currency="USD", price_per_passenger=Decimal(price))
    return ItineraryOption(segments=segments, fare=fare, fares_by_currency={"USD": fare}, source_index=source)


def test_balanced_can_prefer_small_premium_for_direct_flight():
    connecting = make_option("382", 1, 11, 1)
    direct = make_option("386", 0, 8, 2)
    ranked = rank_itineraries([connecting, direct], "balanced")
    assert ranked[0].option.source_index == 2


def test_price_mode_is_strictly_price_first():
    connecting = make_option("382", 1, 11, 1)
    direct = make_option("386", 0, 8, 2)
    ranked = rank_itineraries([connecting, direct], "price")
    assert ranked[0].option.source_index == 1
