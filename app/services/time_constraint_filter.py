from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.models.api import TimeMatchDiagnostics
from app.models.itinerary import ItineraryOption
from app.models.quote_request import (
    SearchLeg,
    TimeConstraint,
    TimeConstraintMode,
    TimeEvent,
)
from app.services.normalizer import itinerary_signature


@dataclass
class TimeFilterResult:
    options: list[ItineraryOption]
    diagnostics: TimeMatchDiagnostics
    distance_by_signature: dict[tuple, int]


def _split_option_legs(
    option: ItineraryOption,
    search_legs: list[SearchLeg],
) -> list[list]:
    if not search_legs:
        return []

    result: list[list] = []
    cursor = 0
    segments = option.segments

    for leg in search_legs:
        leg_segments = []

        while cursor < len(segments):
            segment = segments[cursor]
            leg_segments.append(segment)
            cursor += 1

            if segment.arrival_airport.upper() == leg.destination.upper():
                break

        if not leg_segments:
            return []

        if leg_segments[0].departure_airport.upper() != leg.origin.upper():
            return []

        if leg_segments[-1].arrival_airport.upper() != leg.destination.upper():
            return []

        result.append(leg_segments)

    if cursor != len(segments):
        return []

    return result


def _event_datetime(
    option: ItineraryOption,
    search_legs: list[SearchLeg],
    constraint: TimeConstraint,
) -> datetime | None:
    legs = _split_option_legs(option, search_legs)

    if constraint.leg_index >= len(legs):
        return None

    leg_segments = legs[constraint.leg_index]
    if not leg_segments:
        return None

    if constraint.event == TimeEvent.DEPARTURE:
        return leg_segments[0].departure_at

    return leg_segments[-1].arrival_at


def _constraint_window(
    constraint: TimeConstraint,
    actual: datetime,
) -> tuple[datetime, datetime]:
    anchor_date = constraint.date or actual.date()

    if constraint.time_from is None and constraint.time_to is None:
        return (
            datetime.combine(anchor_date, time.min),
            datetime.combine(anchor_date, time.max),
        )

    start_time = constraint.time_from or time.min
    end_time = constraint.time_to or time.max

    start = datetime.combine(anchor_date, start_time)
    end = datetime.combine(anchor_date, end_time)

    if constraint.wraps_midnight or end < start:
        end += timedelta(days=1)

    return start, end


def _distance_minutes(
    actual: datetime | None,
    constraint: TimeConstraint,
) -> int:
    if actual is None:
        return 10**9

    start, end = _constraint_window(constraint, actual)

    if start <= actual <= end:
        return 0

    if actual < start:
        delta = start - actual
    else:
        delta = actual - end

    return max(1, round(delta.total_seconds() / 60))


def _option_constraint_distances(
    option: ItineraryOption,
    search_legs: list[SearchLeg],
    constraints: list[TimeConstraint],
) -> list[int]:
    return [
        _distance_minutes(
            _event_datetime(option, search_legs, constraint),
            constraint,
        )
        for constraint in constraints
    ]


def _human_event(constraint: TimeConstraint) -> str:
    return "salida" if constraint.event == TimeEvent.DEPARTURE else "llegada"


def _human_target(constraint: TimeConstraint) -> str:
    parts = []

    if constraint.date:
        parts.append(constraint.date.strftime("%d%b").upper())

    if constraint.label:
        parts.append(constraint.label)
    elif constraint.time_from or constraint.time_to:
        if constraint.time_from and constraint.time_to:
            parts.append(
                f"{constraint.time_from.strftime('%H:%M')}-"
                f"{constraint.time_to.strftime('%H:%M')}"
            )
        elif constraint.time_from:
            parts.append(f"desde {constraint.time_from.strftime('%H:%M')}")
        elif constraint.time_to:
            parts.append(f"hasta {constraint.time_to.strftime('%H:%M')}")

    return " ".join(parts) or "horario solicitado"


def apply_time_constraints(
    options: list[ItineraryOption],
    search_legs: list[SearchLeg],
    constraints: list[TimeConstraint],
) -> TimeFilterResult:
    if not constraints:
        return TimeFilterResult(
            options=options,
            diagnostics=TimeMatchDiagnostics(
                status="not_requested",
                candidate_count=len(options),
                exact_match_count=len(options),
                selected_count=len(options),
            ),
            distance_by_signature={
                itinerary_signature(option): 0 for option in options
            },
        )

    required = [
        item for item in constraints
        if item.mode == TimeConstraintMode.REQUIRED
    ]
    preferred = [
        item for item in constraints
        if item.mode == TimeConstraintMode.PREFERRED
    ]

    distances_by_option: dict[tuple, tuple[int, int, bool]] = {}
    exact_options: list[ItineraryOption] = []

    for option in options:
        required_distances = _option_constraint_distances(
            option, search_legs, required
        )
        preferred_distances = _option_constraint_distances(
            option, search_legs, preferred
        )

        required_total = sum(required_distances)
        preferred_total = sum(preferred_distances)
        required_exact = all(distance == 0 for distance in required_distances)

        key = itinerary_signature(option)
        distances_by_option[key] = (
            required_total,
            preferred_total,
            required_exact,
        )

        if required_exact:
            exact_options.append(option)

    candidate_count = len(options)
    exact_match_count = len(exact_options)
    messages: list[str] = []

    if required and exact_options:
        selected = exact_options
        status = "exact"
        fallback_used = False
    elif required:
        selected = options
        status = "fallback"
        fallback_used = True

        for constraint in required:
            messages.append(
                "No hubo coincidencia exacta para "
                f"{_human_event(constraint)} {_human_target(constraint)}; "
                "se muestran las alternativas más cercanas."
            )
    else:
        selected = options
        status = "exact"
        fallback_used = False

    distance_by_signature: dict[tuple, int] = {}

    for option in selected:
        key = itinerary_signature(option)
        required_total, preferred_total, _ = distances_by_option[key]
        distance_by_signature[key] = required_total * 1000 + preferred_total

    selected = sorted(
        selected,
        key=lambda option: (
            distance_by_signature.get(itinerary_signature(option), 10**12),
            itinerary_signature(option),
        ),
    )

    return TimeFilterResult(
        options=selected,
        diagnostics=TimeMatchDiagnostics(
            status=status,
            fallback_used=fallback_used,
            candidate_count=candidate_count,
            exact_match_count=exact_match_count,
            selected_count=len(selected),
            messages=messages,
        ),
        distance_by_signature=distance_by_signature,
    )


def reorder_ranked_by_time(
    ranked: list,
    distance_by_signature: dict[tuple, int],
) -> list:
    if not distance_by_signature:
        return ranked

    return sorted(
        ranked,
        key=lambda item: (
            distance_by_signature.get(
                itinerary_signature(item.option),
                10**12,
            ),
            item.rank,
        ),
    )
