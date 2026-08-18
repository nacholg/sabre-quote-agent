from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services.normalizer import itinerary_signature
from app.services.ranking import (
    commercial_rank_itineraries,
    rank_itineraries,
)


def make_option(
    price: str,
    stops: int,
    duration_hours: int,
    source: int,
) -> ItineraryOption:
    start = datetime(2027, 2, 10, 20, 0, tzinfo=timezone.utc)
    segments = []
    current = start

    for index in range(stops + 1):
        if index == stops:
            end = start + timedelta(hours=duration_hours)
        else:
            end = current + timedelta(hours=2)

        segments.append(
            FlightSegment(
                marketing_carrier=f"X{source}",
                operating_carrier=f"X{source}",
                flight_number=str(100 + index),
                departure_airport="AAA" if index == 0 else f"X{index}",
                arrival_airport="BBB" if index == stops else f"X{index + 1}",
                departure_at=current,
                arrival_at=end,
            )
        )
        current = end + timedelta(hours=1)

    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal(price),
        total_price=Decimal(price),
    )

    return ItineraryOption(
        segments=segments,
        fare=fare,
        fares_by_currency={"USD": fare},
        source_index=source,
    )


def commercial(options, *, distances=None, has_time=False):
    baseline = rank_itineraries(
        options,
        mode="balanced",
        preferred_currency="USD",
    )
    return commercial_rank_itineraries(
        baseline,
        time_distance_by_signature=distances,
        has_time_constraints=has_time,
    )


def test_small_price_premium_can_be_worth_direct_and_faster():
    connection = make_option("450", 1, 11, 1)
    direct = make_option("470", 0, 8, 2)

    ranked = commercial([connection, direct])

    assert ranked[0].option.source_index == 2


def test_large_price_premium_does_not_automatically_win():
    connection = make_option("450", 1, 11, 1)
    expensive_direct = make_option("600", 0, 8, 2)

    ranked = commercial([connection, expensive_direct])

    assert ranked[0].option.source_index == 1


def test_schedule_preference_can_outweigh_small_price_difference():
    cheap_bad_schedule = make_option("480", 0, 8, 1)
    slightly_more_best_schedule = make_option("500", 0, 8, 2)

    distances = {
        itinerary_signature(cheap_bad_schedule): 120,
        itinerary_signature(slightly_more_best_schedule): 10,
    }

    ranked = commercial(
        [cheap_bad_schedule, slightly_more_best_schedule],
        distances=distances,
        has_time=True,
    )

    assert ranked[0].option.source_index == 2


def test_without_temporal_intent_schedule_distances_are_ignored():
    cheaper = make_option("480", 0, 8, 1)
    expensive = make_option("500", 0, 8, 2)

    distances = {
        itinerary_signature(cheaper): 120,
        itinerary_signature(expensive): 10,
    }

    ranked = commercial(
        [cheaper, expensive],
        distances=distances,
        has_time=False,
    )

    assert ranked[0].option.source_index == 1


def test_commercial_scores_are_monotonic_after_sort():
    options = [
        make_option("500", 0, 8, 1),
        make_option("470", 1, 10, 2),
        make_option("620", 0, 7, 3),
    ]

    ranked = commercial(options)

    scores = [item.score for item in ranked]
    assert scores == sorted(scores)
    assert [item.rank for item in ranked] == [1, 2, 3]
