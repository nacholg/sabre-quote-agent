from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services.normalizer import itinerary_signature
from app.services.ranking import (
    CommercialLabel,
    assign_commercial_labels,
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


def labels(item):
    return set(item.commercial_labels)


def test_commercial_labels_are_relative_and_do_not_change_order():
    cheapest_connecting = make_option("450", 1, 11, 1)
    recommended_direct = make_option("470", 0, 8, 2)
    expensive_slow = make_option("600", 2, 14, 3)

    ranked = rank_itineraries(
        [
            cheapest_connecting,
            recommended_direct,
            expensive_slow,
        ],
        mode="balanced",
        preferred_currency="USD",
    )

    original_order = [item.option.source_index for item in ranked]
    labeled = assign_commercial_labels(ranked)

    assert [item.option.source_index for item in labeled] == original_order

    first = labeled[0]
    assert CommercialLabel.RECOMMENDED in labels(first)

    cheapest = next(
        item for item in labeled if item.option.source_index == 1
    )
    assert CommercialLabel.LOWEST_PRICE in labels(cheapest)

    direct = next(
        item for item in labeled if item.option.source_index == 2
    )
    assert CommercialLabel.FASTEST in labels(direct)
    assert CommercialLabel.FEWEST_STOPS in labels(direct)

    assert all(
        CommercialLabel.BEST_SCHEDULE not in labels(item)
        for item in labeled
    )


def test_best_schedule_is_only_added_when_temporal_intent_exists():
    first = make_option("500", 0, 9, 1)
    second = make_option("520", 0, 9, 2)

    ranked = rank_itineraries(
        [first, second],
        mode="price",
        preferred_currency="USD",
    )

    distances = {
        itinerary_signature(first): 75,
        itinerary_signature(second): 10,
    }

    without_time = assign_commercial_labels(
        ranked,
        time_distance_by_signature=distances,
        has_time_constraints=False,
    )

    assert all(
        CommercialLabel.BEST_SCHEDULE not in labels(item)
        for item in without_time
    )

    with_time = assign_commercial_labels(
        ranked,
        time_distance_by_signature=distances,
        has_time_constraints=True,
    )

    best_schedule = next(
        item
        for item in with_time
        if CommercialLabel.BEST_SCHEDULE in labels(item)
    )

    assert best_schedule.option.source_index == 2


def test_ties_can_share_objective_labels():
    first = make_option("500", 0, 8, 1)
    second = make_option("500", 0, 8, 2)

    ranked = rank_itineraries(
        [first, second],
        mode="balanced",
        preferred_currency="USD",
    )

    labeled = assign_commercial_labels(ranked)

    for item in labeled:
        assert CommercialLabel.LOWEST_PRICE in labels(item)
        assert CommercialLabel.FASTEST in labels(item)
        assert CommercialLabel.FEWEST_STOPS in labels(item)

    assert sum(
        CommercialLabel.RECOMMENDED in labels(item)
        for item in labeled
    ) == 1
