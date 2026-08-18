from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.models.itinerary import ItineraryOption


class RankingMode(str, Enum):
    BALANCED = "balanced"
    PRICE = "price"
    DURATION = "duration"
    STOPS = "stops"


@dataclass(frozen=True)
class RankedItinerary:
    option: ItineraryOption
    rank: int
    score: Decimal
    stops: int
    duration_minutes: int
    ranking_currency: str
    ranking_price: Decimal


def _journey_groups(option: ItineraryOption):
    """Split ticket segments into flown journeys, excluding destination stopovers.

    A gap over 24h is treated as a new requested leg (return/open-jaw/circle-trip),
    so days spent at destination do not inflate duration or stop counts.
    """
    if not option.segments:
        return []
    groups = [[option.segments[0]]]
    for segment in option.segments[1:]:
        previous = groups[-1][-1]
        gap = segment.departure_at - previous.arrival_at
        if gap.total_seconds() > 24 * 3600 or segment.departure_airport != previous.arrival_airport:
            groups.append([segment])
        else:
            groups[-1].append(segment)
    return groups


def itinerary_duration_minutes(option: ItineraryOption) -> int:
    total = 0
    for group in _journey_groups(option):
        delta = group[-1].arrival_at - group[0].departure_at
        total += max(0, int(delta.total_seconds() // 60))
    return total


def itinerary_stops(option: ItineraryOption) -> int:
    return sum(max(0, len(group) - 1) for group in _journey_groups(option))


def _ranking_fare(option: ItineraryOption, preferred_currency: str = "USD"):
    fares = option.fares_by_currency or {option.fare.currency: option.fare}
    if preferred_currency in fares:
        return fares[preferred_currency]
    if "USD" in fares:
        return fares["USD"]
    if "ARS" in fares:
        return fares["ARS"]
    return option.fare


def rank_itineraries(
    options: list[ItineraryOption],
    mode: RankingMode | str = RankingMode.BALANCED,
    preferred_currency: str = "USD",
) -> list[RankedItinerary]:
    if not options:
        return []

    mode = RankingMode(mode)
    rows = []
    for option in options:
        fare = _ranking_fare(option, preferred_currency)
        rows.append(
            {
                "option": option,
                "price": fare.price_per_passenger,
                "currency": fare.currency,
                "stops": itinerary_stops(option),
                "duration": itinerary_duration_minutes(option),
            }
        )

    positive_prices = [row["price"] for row in rows if row["price"] > 0]
    min_price = min(positive_prices) if positive_prices else Decimal("1")
    positive_durations = [row["duration"] for row in rows if row["duration"] > 0]
    min_duration = min(positive_durations) if positive_durations else 1

    for row in rows:
        price_ratio = row["price"] / min_price if min_price else Decimal("1")
        duration_extra_hours = Decimal(max(0, row["duration"] - min_duration)) / Decimal("60")

        # Balanced heuristic is intentionally simple and auditable:
        # - price is the baseline (ratio to cheapest option)
        # - each stop adds 15%
        # - each hour above the fastest option adds 3%
        balanced_score = (
            price_ratio
            + Decimal(row["stops"]) * Decimal("0.15")
            + duration_extra_hours * Decimal("0.03")
        )

        if mode == RankingMode.PRICE:
            score = row["price"]
            sort_key = (score, row["stops"], row["duration"])
        elif mode == RankingMode.DURATION:
            score = Decimal(row["duration"])
            sort_key = (row["duration"], row["stops"], row["price"])
        elif mode == RankingMode.STOPS:
            score = Decimal(row["stops"])
            sort_key = (row["stops"], row["price"], row["duration"])
        else:
            score = balanced_score
            sort_key = (balanced_score, row["stops"], row["price"], row["duration"])

        row["score"] = score
        row["sort_key"] = sort_key

    rows.sort(key=lambda row: row["sort_key"])

    return [
        RankedItinerary(
            option=row["option"],
            rank=index,
            score=row["score"],
            stops=row["stops"],
            duration_minutes=row["duration"],
            ranking_currency=row["currency"],
            ranking_price=row["price"],
        )
        for index, row in enumerate(rows, start=1)
    ]
