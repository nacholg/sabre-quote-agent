from __future__ import annotations

import re
import unicodedata
from datetime import date, time, timedelta

from app.models.quote_request import DayPart, TimeConstraint, TimeConstraintMode, TimeEvent

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

PREFERRED_MARKERS = ("preferentemente", "preferible", "idealmente", "si puede ser", "si es posible")


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _year_for(month: int, day: int, today: date, explicit_year: int | None = None) -> int:
    if explicit_year:
        return explicit_year
    candidate = date(today.year, month, day)
    return today.year if candidate >= today else today.year + 1


def _date_value(day: str, month: str, year: str | None, today: date) -> date:
    m = MONTHS[month]
    return date(_year_for(m, int(day), today, int(year) if year else None), m, int(day))


def _mode(before: str, after: str = "") -> TimeConstraintMode:
    context = _fold(before[-80:] + " " + after[:80])
    return TimeConstraintMode.PREFERRED if any(x in context for x in PREFERRED_MARKERS) else TimeConstraintMode.REQUIRED


def _date_matches(text: str):
    pattern = re.compile(
        r"\b(?:el\s+)?(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"(?:\s+(?:de\s+)?(20\d{2}))?"
        r"(?:\s+(?:a\s+la|por\s+la|por)\s+(madrugada|manana|mediodia|tarde|noche))?"
    )
    return list(pattern.finditer(text))

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

    # ---------------------------------------------------------
    # IDA
    # ---------------------------------------------------------
    if out_matches:
        match = out_matches[-1]

        before = outbound[:match.start()]
        after = outbound[match.end():]

        # Una fecha por sí sola NO es una restricción horaria.
        #
        # Ejemplo:
        #   "del 19 al 30 de septiembre"
        #
        # debe seguir siendo manejado por _parse_dates().
        #
        # Sólo intervenimos cuando existe una intención temporal:
        #   "llegando el 11 de febrero"
        #   "saliendo el 10 de febrero"
        #   "el 10 de febrero por la noche"
        temporal_cue = bool(
            match.group(4)
            or temporal_event_pattern.search(
                before + " " + after[:60]
            )
        )

        if temporal_cue:
            event = (
                TimeEvent.ARRIVAL
                if re.search(
                    r"\b("
                    r"lleguen|llegar|llegando|llegada"
                    r")\b",
                    before,
                )
                else TimeEvent.DEPARTURE
            )

            d = _date_value(
                match.group(1),
                match.group(2),
                match.group(3),
                today,
            )

            part, start, end, wraps = DAYPARTS.get(
                match.group(4),
                (None, None, None, False),
            )

            constraints.append(
                TimeConstraint(
                    leg_index=0,
                    event=event,
                    date=d,
                    time_from=start,
                    time_to=end,
                    daypart=part,
                    mode=_mode(before, after),
                    wraps_midnight=wraps,
                    label=match.group(4),
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

    # ---------------------------------------------------------
    # REGRESO
    # ---------------------------------------------------------
    if in_matches:
        first = in_matches[0]

        before = inbound[:first.start()]
        after = inbound[first.end():]

        temporal_cue = bool(
            first.group(4)
            or temporal_event_pattern.search(
                before + " " + after[:60]
            )
        )

        if temporal_cue:
            d = _date_value(
                first.group(1),
                first.group(2),
                first.group(3),
                today,
            )

            part, start, end, wraps = DAYPARTS.get(
                first.group(4),
                (None, None, None, False),
            )

            constraints.append(
                TimeConstraint(
                    leg_index=1,
                    event=TimeEvent.DEPARTURE,
                    date=d,
                    time_from=start,
                    time_to=end,
                    daypart=part,
                    mode=_mode(before, after),
                    wraps_midnight=wraps,
                    label=first.group(4),
                )
            )

            inferred_return = d

        # Puede existir además una restricción de llegada del regreso:
        #
        # "regreso el 20 de febrero por la noche,
        #  llegando el 21 de febrero"
        if len(in_matches) >= 2:
            second = in_matches[1]

            prefix = inbound[
                max(0, second.start() - 40):
                second.start()
            ]

            suffix = inbound[
                second.end():
                second.end() + 40
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

                part2, start2, end2, wraps2 = DAYPARTS.get(
                    second.group(4),
                    (None, None, None, False),
                )

                constraints.append(
                    TimeConstraint(
                        leg_index=1,
                        event=TimeEvent.ARRIVAL,
                        date=d2,
                        time_from=start2,
                        time_to=end2,
                        daypart=part2,
                        mode=_mode(prefix, suffix),
                        wraps_midnight=wraps2,
                        label=second.group(4),
                    )
                )

    return (
        constraints,
        inferred_departure,
        inferred_return,
        assumptions,
    )