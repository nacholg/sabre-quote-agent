from datetime import date, datetime, time
from decimal import Decimal

from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.models.quote_request import (
    DayPart,
    SearchLeg,
    TimeConstraint,
    TimeConstraintMode,
    TimeEvent,
)
from app.services.normalizer import itinerary_signature
from app.services.time_constraint_filter import apply_time_constraints


def _fare() -> FareOption:
    return FareOption(
        cabin="economy",
        cabin_codes=["Y"],
        currency="USD",
        price_per_passenger=Decimal("1000"),
        total_price=Decimal("1000"),
    )


def _round_trip(
    *,
    outbound_departure: datetime,
    outbound_arrival: datetime,
    return_departure: datetime,
    return_arrival: datetime,
    carrier: str = "AA",
) -> ItineraryOption:
    fare = _fare()

    return ItineraryOption(
        segments=[
            FlightSegment(
                marketing_carrier=carrier,
                operating_carrier=carrier,
                flight_number="900",
                departure_airport="EZE",
                arrival_airport="MIA",
                departure_at=outbound_departure,
                arrival_at=outbound_arrival,
            ),
            FlightSegment(
                marketing_carrier=carrier,
                operating_carrier=carrier,
                flight_number="907",
                departure_airport="MIA",
                arrival_airport="EZE",
                departure_at=return_departure,
                arrival_at=return_arrival,
            ),
        ],
        fare=fare,
        fares_by_currency={"USD": fare},
        fare_options_by_currency={"USD": [fare]},
        source_index=1,
    )


LEGS = [
    SearchLeg(
        origin="EZE",
        destination="MIA",
        departure_date=date(2027, 2, 10),
    ),
    SearchLeg(
        origin="MIA",
        destination="EZE",
        departure_date=date(2027, 2, 20),
    ),
]


def test_exact_arrival_and_night_departure_are_kept():
    option = _round_trip(
        outbound_departure=datetime(2027, 2, 10, 22, 0),
        outbound_arrival=datetime(2027, 2, 11, 7, 30),
        return_departure=datetime(2027, 2, 20, 22, 0),
        return_arrival=datetime(2027, 2, 21, 7, 0),
    )

    constraints = [
        TimeConstraint(
            leg_index=0,
            event=TimeEvent.ARRIVAL,
            date=date(2027, 2, 11),
            daypart=DayPart.MORNING,
            time_from=time(6, 0),
            time_to=time(11, 59),
        ),
        TimeConstraint(
            leg_index=1,
            event=TimeEvent.DEPARTURE,
            date=date(2027, 2, 20),
            daypart=DayPart.NIGHT,
            time_from=time(19, 0),
            time_to=time(2, 59),
            wraps_midnight=True,
        ),
    ]

    result = apply_time_constraints([option], LEGS, constraints)

    assert result.diagnostics.status == "exact"
    assert result.diagnostics.fallback_used is False
    assert result.diagnostics.exact_match_count == 1
    assert result.options == [option]


def test_night_of_20_accepts_early_hours_of_21():
    option = _round_trip(
        outbound_departure=datetime(2027, 2, 10, 22, 0),
        outbound_arrival=datetime(2027, 2, 11, 7, 30),
        return_departure=datetime(2027, 2, 21, 1, 15),
        return_arrival=datetime(2027, 2, 21, 10, 0),
    )

    constraint = TimeConstraint(
        leg_index=1,
        event=TimeEvent.DEPARTURE,
        date=date(2027, 2, 20),
        daypart=DayPart.NIGHT,
        time_from=time(19, 0),
        time_to=time(2, 59),
        wraps_midnight=True,
    )

    result = apply_time_constraints([option], LEGS, [constraint])

    assert result.diagnostics.status == "exact"
    assert result.diagnostics.exact_match_count == 1


def test_required_constraint_falls_back_to_nearest_option():
    near = _round_trip(
        outbound_departure=datetime(2027, 2, 10, 23, 0),
        outbound_arrival=datetime(2027, 2, 11, 12, 20),
        return_departure=datetime(2027, 2, 20, 20, 0),
        return_arrival=datetime(2027, 2, 21, 7, 0),
        carrier="AA",
    )

    far = _round_trip(
        outbound_departure=datetime(2027, 2, 10, 23, 0),
        outbound_arrival=datetime(2027, 2, 11, 16, 0),
        return_departure=datetime(2027, 2, 20, 20, 0),
        return_arrival=datetime(2027, 2, 21, 7, 0),
        carrier="DL",
    )

    constraint = TimeConstraint(
        leg_index=0,
        event=TimeEvent.ARRIVAL,
        date=date(2027, 2, 11),
        daypart=DayPart.MORNING,
        time_from=time(6, 0),
        time_to=time(11, 59),
        mode=TimeConstraintMode.REQUIRED,
    )

    result = apply_time_constraints([far, near], LEGS, [constraint])

    assert result.diagnostics.status == "fallback"
    assert result.diagnostics.fallback_used is True
    assert result.diagnostics.exact_match_count == 0
    assert result.options[0] is near

    near_distance = result.distance_by_signature[itinerary_signature(near)]
    far_distance = result.distance_by_signature[itinerary_signature(far)]
    assert near_distance < far_distance


def test_preferred_constraint_does_not_remove_options():
    morning = _round_trip(
        outbound_departure=datetime(2027, 2, 10, 22, 0),
        outbound_arrival=datetime(2027, 2, 11, 8, 0),
        return_departure=datetime(2027, 2, 20, 20, 0),
        return_arrival=datetime(2027, 2, 21, 7, 0),
        carrier="AA",
    )

    afternoon = _round_trip(
        outbound_departure=datetime(2027, 2, 10, 22, 0),
        outbound_arrival=datetime(2027, 2, 11, 15, 0),
        return_departure=datetime(2027, 2, 20, 20, 0),
        return_arrival=datetime(2027, 2, 21, 7, 0),
        carrier="DL",
    )

    constraint = TimeConstraint(
        leg_index=0,
        event=TimeEvent.ARRIVAL,
        date=date(2027, 2, 11),
        daypart=DayPart.MORNING,
        time_from=time(6, 0),
        time_to=time(11, 59),
        mode=TimeConstraintMode.PREFERRED,
    )

    result = apply_time_constraints([afternoon, morning], LEGS, [constraint])

    assert result.diagnostics.status == "exact"
    assert len(result.options) == 2
    assert result.options[0] is morning


def test_required_constraints_must_all_match():
    correct = _round_trip(
        outbound_departure=datetime(2027, 2, 10, 22, 0),
        outbound_arrival=datetime(2027, 2, 11, 8, 0),
        return_departure=datetime(2027, 2, 20, 21, 0),
        return_arrival=datetime(2027, 2, 21, 7, 0),
    )

    wrong_return = _round_trip(
        outbound_departure=datetime(2027, 2, 10, 22, 0),
        outbound_arrival=datetime(2027, 2, 11, 8, 0),
        return_departure=datetime(2027, 2, 20, 14, 0),
        return_arrival=datetime(2027, 2, 21, 1, 0),
        carrier="DL",
    )

    constraints = [
        TimeConstraint(
            leg_index=0,
            event=TimeEvent.ARRIVAL,
            date=date(2027, 2, 11),
            time_from=time(6, 0),
            time_to=time(11, 59),
        ),
        TimeConstraint(
            leg_index=1,
            event=TimeEvent.DEPARTURE,
            date=date(2027, 2, 20),
            time_from=time(19, 0),
            time_to=time(2, 59),
            wraps_midnight=True,
        ),
    ]

    result = apply_time_constraints([wrong_return, correct], LEGS, constraints)

    assert result.diagnostics.status == "exact"
    assert result.options == [correct]
