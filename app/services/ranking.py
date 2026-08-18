from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum

from app.models.itinerary import ItineraryOption
from app.services.normalizer import itinerary_signature


class RankingMode(str, Enum):
    BALANCED = "balanced"
    PRICE = "price"
    DURATION = "duration"
    STOPS = "stops"



class CommercialLabel(str, Enum):
    RECOMMENDED = "recommended"
    LOWEST_PRICE = "lowest_price"
    FASTEST = "fastest"
    FEWEST_STOPS = "fewest_stops"
    BEST_SCHEDULE = "best_schedule"


@dataclass(frozen=True)
class RankedItinerary:
    option: ItineraryOption
    rank: int
    score: Decimal
    stops: int
    duration_minutes: int
    ranking_currency: str
    ranking_price: Decimal
    commercial_labels: tuple[CommercialLabel, ...] = ()


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



def commercial_rank_itineraries(
    ranked: list[RankedItinerary],
    *,
    time_distance_by_signature: dict[tuple, int] | None = None,
    has_time_constraints: bool = False,
) -> list[RankedItinerary]:
    """
    Commercial ranking for BALANCED mode.

    Auditable heuristic:
    - price remains the baseline as ratio to the cheapest option
    - each stop adds 18%
    - each hour above the fastest option adds 2.5%
    - when temporal intent exists, schedule position can add up to 30%

    Explicit PRICE / DURATION / STOPS modes are handled before this function
    and are intentionally not changed here.
    """
    if not ranked:
        return []

    positive_prices = [
        item.ranking_price
        for item in ranked
        if item.ranking_price > 0
    ]
    min_price = min(positive_prices) if positive_prices else Decimal("1")

    positive_durations = [
        item.duration_minutes
        for item in ranked
        if item.duration_minutes > 0
    ]
    min_duration = min(positive_durations) if positive_durations else 1

    schedule_position: dict[tuple, int] = {}
    if has_time_constraints and time_distance_by_signature:
        ordered_signatures = sorted(
            (
                itinerary_signature(item.option)
                for item in ranked
            ),
            key=lambda signature: (
                time_distance_by_signature.get(signature, 10**12),
                signature,
            ),
        )
        schedule_position = {
            signature: index
            for index, signature in enumerate(ordered_signatures)
        }

    denominator = max(1, len(ranked) - 1)
    rows: list[tuple[tuple, RankedItinerary, Decimal]] = []

    for item in ranked:
        price_ratio = (
            item.ranking_price / min_price
            if min_price
            else Decimal("1")
        )

        duration_extra_hours = (
            Decimal(
                max(0, item.duration_minutes - min_duration)
            )
            / Decimal("60")
        )

        stop_penalty = (
            Decimal(item.stops) * Decimal("0.18")
        )
        duration_penalty = (
            duration_extra_hours * Decimal("0.025")
        )

        schedule_penalty = Decimal("0")
        signature = itinerary_signature(item.option)

        if schedule_position:
            normalized_position = (
                Decimal(schedule_position[signature])
                / Decimal(denominator)
            )
            schedule_penalty = (
                normalized_position * Decimal("0.30")
            )

        commercial_score = (
            price_ratio
            + stop_penalty
            + duration_penalty
            + schedule_penalty
        )

        sort_key = (
            commercial_score,
            item.stops,
            item.ranking_price,
            item.duration_minutes,
        )

        rows.append(
            (
                sort_key,
                item,
                commercial_score,
            )
        )

    rows.sort(key=lambda row: row[0])

    return [
        replace(
            item,
            rank=index,
            score=commercial_score,
        )
        for index, (_sort_key, item, commercial_score)
        in enumerate(rows, start=1)
    ]

def assign_commercial_labels(
    ranked: list[RankedItinerary],
    *,
    time_distance_by_signature: dict[tuple, int] | None = None,
    has_time_constraints: bool = False,
) -> list[RankedItinerary]:
    if not ranked:
        return []

    min_price = min(item.ranking_price for item in ranked)
    min_duration = min(item.duration_minutes for item in ranked)
    min_stops = min(item.stops for item in ranked)

    best_schedule_signature = None
    if has_time_constraints and time_distance_by_signature:
        best_schedule_item = min(
            ranked,
            key=lambda item: (
                time_distance_by_signature.get(
                    itinerary_signature(item.option),
                    10**12,
                ),
                item.rank,
            ),
        )
        best_schedule_signature = itinerary_signature(
            best_schedule_item.option
        )

    labeled: list[RankedItinerary] = []

    for index, item in enumerate(ranked):
        labels: list[CommercialLabel] = []

        if index == 0:
            labels.append(CommercialLabel.RECOMMENDED)

        if item.ranking_price == min_price:
            labels.append(CommercialLabel.LOWEST_PRICE)

        if item.duration_minutes == min_duration:
            labels.append(CommercialLabel.FASTEST)

        if item.stops == min_stops:
            labels.append(CommercialLabel.FEWEST_STOPS)

        if (
            best_schedule_signature is not None
            and itinerary_signature(item.option)
            == best_schedule_signature
        ):
            labels.append(CommercialLabel.BEST_SCHEDULE)

        labeled.append(
            replace(
                item,
                commercial_labels=tuple(labels),
            )
        )

    return labeled
