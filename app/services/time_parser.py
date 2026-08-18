from __future__ import annotations

import re
import unicodedata
from datetime import date, time, timedelta

from app.models.quote_request import (
    DayPart,
    TimeConstraint,
    TimeConstraintMode,
    TimeEvent,
)

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

DAYPARTS = {
    "madrugada": (DayPart.DAWN, time(0, 0), time(5, 59), False),
    "manana": (DayPart.MORNING, time(6, 0), time(11, 59), False),
    "mediodia": (DayPart.MIDDAY, time(11, 0), time(14, 0), False),
    "tarde": (DayPart.AFTERNOON, time(12, 0), time(18, 59), False),
    "noche": (DayPart.NIGHT, time(19, 0), time(2, 59), True),
}

PREFERRED_MARKERS = (
    "preferentemente",
    "preferible",
    "idealmente",
    "si puede ser",
    "si es posible",
)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _year_for(
    month: int,
    day: int,
    today: date,
    explicit_year: int | None = None,
) -> int:
    if explicit_year:
        return explicit_year
    candidate = date(today.year, month, day)
    return today.year if candidate >= today else today.year + 1


def _date_value(
    day: str,
    month: str,
    year: str | None,
    today: date,
) -> date:
    m = MONTHS[month]
    return date(
        _year_for(
            m,
            int(day),
            today,
            int(year) if year else None,
        ),
        m,
        int(day),
    )


def _mode(before: str, after: str = "") -> TimeConstraintMode:
    context = _fold(before[-80:] + " " + after[:80])
    return (
        TimeConstraintMode.PREFERRED
        if any(x in context for x in PREFERRED_MARKERS)
        else TimeConstraintMode.REQUIRED
    )


def _date_matches(text: str):
    pattern = re.compile(
        r"\b(?:el\s+)?(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|setiembre|octubre|noviembre|diciembre)"
        r"(?:\s+(?:de\s+)?(20\d{2}))?"
        r"(?:\s+(?:a\s+la|por\s+la|por)\s+"
        r"(madrugada|manana|mediodia|tarde|noche))?"
    )
    return list(pattern.finditer(text))


def _clock_time(
    hour_text: str,
    minute_text: str | None = None,
    qualifier: str | None = None,
) -> time:
    hour = int(hour_text)
    minute = int(minute_text or 0)

    qualifier = qualifier or ""
    if qualifier in {"tarde", "noche"} and hour < 12:
        hour += 12
    elif qualifier == "madrugada" and hour == 12:
        hour = 0
    elif qualifier == "manana" and hour == 12:
        hour = 0

    if hour > 23 or minute > 59:
        raise ValueError("Horario fuera de rango.")

    return time(hour, minute)


def _time_expr_context(before: str, after: str) -> str:
    return _fold((before[-70:] + " " + after[:100]).strip())


def _explicit_time_window(
    before: str,
    after: str,
) -> tuple[
    time | None,
    time | None,
    bool,
    str | None,
    bool,
]:
    """
    Returns:
      time_from, time_to, wraps_midnight, label, force_preferred
    """
    context = _time_expr_context(before, after)

    clock = (
        r"(\d{1,2})(?::(\d{2}))?"
        r"(?:\s*(?:hs?|h|horas?))?"
        r"(?:\s+de\s+la\s+(manana|tarde|noche|madrugada))?"
    )

    between = re.search(
        rf"\b(?:entre|desde)\s+(?:las?\s+)?{clock}"
        rf"\s+(?:y|a|hasta)\s+(?:las?\s+)?{clock}\b",
        context,
    )
    if between:
        start = _clock_time(
            between.group(1),
            between.group(2),
            between.group(3),
        )
        end = _clock_time(
            between.group(4),
            between.group(5),
            between.group(6),
        )
        wraps = end < start
        return (
            start,
            end,
            wraps,
            f"entre {start.strftime('%H:%M')} y {end.strftime('%H:%M')}",
            False,
        )

    after_match = re.search(
        rf"\b(?:despues\s+de|a\s+partir\s+de|no\s+antes\s+de)"
        rf"\s+(?:las?\s+)?{clock}\b",
        context,
    )
    if after_match:
        start = _clock_time(
            after_match.group(1),
            after_match.group(2),
            after_match.group(3),
        )
        return (
            start,
            None,
            False,
            f"desde {start.strftime('%H:%M')}",
            False,
        )

    before_match = re.search(
        rf"\b(?:antes\s+de|hasta|como\s+maximo(?:\s+a)?|"
        rf"no\s+despues\s+de)"
        rf"\s+(?:las?\s+)?{clock}\b",
        context,
    )
    if before_match:
        end = _clock_time(
            before_match.group(1),
            before_match.group(2),
            before_match.group(3),
        )
        return (
            None,
            end,
            False,
            f"hasta {end.strftime('%H:%M')}",
            False,
        )

    around = re.search(
        rf"\b(?:alrededor\s+de|cerca\s+de|aproximadamente)"
        rf"\s+(?:las?\s+)?{clock}\b",
        context,
    )
    if around:
        center = _clock_time(
            around.group(1),
            around.group(2),
            around.group(3),
        )
        center_minutes = center.hour * 60 + center.minute
        start_minutes = max(0, center_minutes - 60)
        end_minutes = min(23 * 60 + 59, center_minutes + 60)
        start = time(start_minutes // 60, start_minutes % 60)
        end = time(end_minutes // 60, end_minutes % 60)
        return (
            start,
            end,
            False,
            f"alrededor de {center.strftime('%H:%M')}",
            True,
        )

    return None, None, False, None, False


def _constraint_parts(
    match,
    before: str,
    after: str,
) -> tuple[
    DayPart | None,
    time | None,
    time | None,
    bool,
    str | None,
    bool,
]:
    explicit_from, explicit_to, explicit_wraps, explicit_label, force_preferred = (
        _explicit_time_window(before, after)
    )

    if explicit_from is not None or explicit_to is not None:
        return (
            None,
            explicit_from,
            explicit_to,
            explicit_wraps,
            explicit_label,
            force_preferred,
        )

    part, start, end, wraps = DAYPARTS.get(
        match.group(4),
        (None, None, None, False),
    )
    return part, start, end, wraps, match.group(4), False


def parse_time_constraints(text: str, *, today: date):
    folded = _fold(text)
    constraints: list[TimeConstraint] = []
    assumptions: list[str] = []

    split = re.search(
        r"\b(?:con\s+regreso|regreso|vuelta)\b",
        folded,
    )
    idx = split.start() if split else None

    outbound = folded if idx is None else folded[:idx]
    inbound = "" if idx is None else folded[idx:]

    out_matches = _date_matches(outbound)
    in_matches = _date_matches(inbound)

    inferred_departure = None
    inferred_return = None

    temporal_event_pattern = re.compile(
        r"\b("
        r"lleguen|llegar|llegando|llegada|"
        r"salir|saliendo|salida|sale|salen"
        r")\b"
    )

    if out_matches:
        match = out_matches[-1]
        before = outbound[:match.start()]
        after = outbound[match.end():]

        explicit_from, explicit_to, _, _, _ = _explicit_time_window(
            before,
            after,
        )

        arrival_cue = bool(
            re.search(
                r"\b(lleguen|llegar|llegando|llegada)\b",
                before,
            )
        )
        temporal_cue = bool(
            match.group(4)
            or explicit_from is not None
            or explicit_to is not None
            or arrival_cue
        )

        if temporal_cue:
            event = (
                TimeEvent.ARRIVAL
                if arrival_cue
                else TimeEvent.DEPARTURE
            )

            d = _date_value(
                match.group(1),
                match.group(2),
                match.group(3),
                today,
            )

            part, start, end, wraps, label, force_preferred = _constraint_parts(
                match,
                before,
                after,
            )

            mode = (
                TimeConstraintMode.PREFERRED
                if force_preferred
                else _mode(before, after)
            )

            constraints.append(
                TimeConstraint(
                    leg_index=0,
                    event=event,
                    date=d,
                    time_from=start,
                    time_to=end,
                    daypart=part,
                    mode=mode,
                    wraps_midnight=wraps,
                    label=label,
                )
            )

            if event == TimeEvent.ARRIVAL:
                inferred_departure = d - timedelta(days=1)
                assumptions.append(
                    f"Salida de ida inferida: "
                    f"{inferred_departure.isoformat()} "
                    f"para buscar llegada el {d.isoformat()}."
                )
            else:
                inferred_departure = d

    if in_matches:
        first = in_matches[0]
        before = inbound[:first.start()]
        after = inbound[first.end():]

        explicit_from, explicit_to, _, _, _ = _explicit_time_window(
            before,
            after,
        )

        temporal_cue = bool(
            first.group(4)
            or explicit_from is not None
            or explicit_to is not None
            or temporal_event_pattern.search(
                before + " " + after[:80]
            )
        )

        if temporal_cue:
            d = _date_value(
                first.group(1),
                first.group(2),
                first.group(3),
                today,
            )

            part, start, end, wraps, label, force_preferred = _constraint_parts(
                first,
                before,
                after,
            )

            mode = (
                TimeConstraintMode.PREFERRED
                if force_preferred
                else _mode(before, after)
            )

            constraints.append(
                TimeConstraint(
                    leg_index=1,
                    event=TimeEvent.DEPARTURE,
                    date=d,
                    time_from=start,
                    time_to=end,
                    daypart=part,
                    mode=mode,
                    wraps_midnight=wraps,
                    label=label,
                )
            )
            inferred_return = d

        if len(in_matches) >= 2:
            second = in_matches[1]
            prefix = inbound[
                max(0, second.start() - 60):
                second.start()
            ]
            suffix = inbound[
                second.end():
                second.end() + 100
            ]

            if re.search(
                r"\b(llegando|llegada|llegar)\b",
                prefix,
            ):
                d2 = _date_value(
                    second.group(1),
                    second.group(2),
                    second.group(3),
                    today,
                )

                part2, start2, end2, wraps2, label2, force_preferred2 = (
                    _constraint_parts(
                        second,
                        prefix,
                        suffix,
                    )
                )

                mode2 = (
                    TimeConstraintMode.PREFERRED
                    if force_preferred2
                    else _mode(prefix, suffix)
                )

                constraints.append(
                    TimeConstraint(
                        leg_index=1,
                        event=TimeEvent.ARRIVAL,
                        date=d2,
                        time_from=start2,
                        time_to=end2,
                        daypart=part2,
                        mode=mode2,
                        wraps_midnight=wraps2,
                        label=label2,
                    )
                )

    return (
        constraints,
        inferred_departure,
        inferred_return,
        assumptions,
    )
