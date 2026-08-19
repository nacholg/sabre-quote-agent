from datetime import date

from app.models.api import QuoteSearchAPIRequest
from app.models.quote_request import (
    SearchLeg, TripType, infer_trip_type,
)


def leg(origin, destination, day):
    return SearchLeg(
        origin=origin, destination=destination,
        departure_date=date(2026, 12, day),
    )


def test_one_way_inference():
    assert infer_trip_type([leg("EZE", "MIA", 10)]) == TripType.ONE_WAY


def test_round_trip_inference():
    legs = [leg("EZE", "MIA", 10), leg("MIA", "EZE", 30)]
    assert infer_trip_type(legs) == TripType.ROUND_TRIP


def test_round_trip_city_airport_equivalence():
    legs = [leg("BUE", "MIA", 10), leg("MIA", "EZE", 30)]
    assert infer_trip_type(legs) == TripType.ROUND_TRIP


def test_open_jaw_inference():
    legs = [leg("EZE", "MIA", 10), leg("JFK", "EZE", 30)]
    assert infer_trip_type(legs) == TripType.OPEN_JAW


def test_multi_city_inference():
    legs = [
        leg("EZE", "MIA", 10),
        leg("MIA", "NYC", 25),
        leg("JFK", "EZE", 30),
    ]
    assert infer_trip_type(legs) == TripType.MULTI_CITY


def test_api_request_uses_canonical_inference_for_explicit_legs():
    request = QuoteSearchAPIRequest(
        legs=[
            leg("EZE", "MIA", 10),
            leg("MIA", "EZE", 30),
        ]
    )
    search = request.to_search_request()
    assert search.trip_type == TripType.ROUND_TRIP
