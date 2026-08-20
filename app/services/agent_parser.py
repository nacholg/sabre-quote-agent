from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date
from functools import lru_cache
from typing import Iterable

from app.models.api import AgentInterpretation, AgentQuoteRequest, QuoteSearchAPIRequest
from app.models.quote_request import (
    Cabin,
    FarePreference,
    PassengerKind,
    PassengerSpec,
    SearchLeg,
    TripType,
    infer_trip_type,
)
from app.services.pricing_rules import PricingCurrency
from app.services.reference_repository import get_reference_repository
from app.services.time_parser import parse_time_constraints


AIRPORT_ALIASES = {
    "eze": "EZE", "ezeiza": "EZE",
    "aep": "AEP", "aeroparque": "AEP",
    "bue": "BUE", "buenos aires": "BUE",
    "mia": "MIA", "miami": "MIA",
    "jfk": "JFK", "nueva york": "NYC", "new york": "NYC", "nyc": "NYC",
    "dfw": "DFW", "dallas": "DFW",
    "mad": "MAD", "madrid": "MAD",
    "bcn": "BCN", "barcelona": "BCN",
    "lhr": "LHR", "londres": "LON", "london": "LON", "lon": "LON",
    "cdg": "CDG", "paris": "CDG", "parís": "CDG",
    "gru": "GRU", "sao paulo": "GRU", "são paulo": "GRU",
    "gig": "GIG", "rio": "RIO", "rio de janeiro": "RIO",
    "scl": "SCL", "santiago": "SCL",
    "lim": "LIM", "lima": "LIM",
    "bog": "BOG", "bogota": "BOG", "bogotá": "BOG",
    "pty": "PTY", "panama": "PTY", "panamá": "PTY",
    "cor": "COR", "cordoba": "COR", "córdoba": "COR",
    "mdz": "MDZ", "mendoza": "MDZ",
    "brc": "BRC", "bariloche": "BRC",
    "nqn": "NQN", "neuquen": "NQN", "neuquén": "NQN",
    "ush": "USH", "ushuaia": "USH",
    "ros": "ROS", "rosario": "ROS",
    "sla": "SLA", "salta": "SLA",
    "tuc": "TUC", "tucuman": "TUC", "tucumán": "TUC",
    "igr": "IGR", "iguazu": "IGR", "iguazú": "IGR",
    "fte": "FTE", "el calafate": "FTE", "calafate": "FTE",
    "fco": "FCO", "roma": "FCO", "rome": "FCO",
    "tyo": "TYO", "tokyo": "TYO", "tokio": "TYO",
}

CARRIER_ALIASES = {
    "aa": "AA", "american": "AA", "american airlines": "AA",
    "ar": "AR", "aerolineas": "AR", "aerolíneas": "AR",
    "aerolineas argentinas": "AR", "aerolíneas argentinas": "AR",
    "latam": "LA", "latam airlines": "LA",
    "g3": "G3", "gol": "G3", "gol linhas aereas": "G3",
    "gol linhas aéreas": "G3", "gol linhas": "G3",
    "ib": "IB", "iberia": "IB",
    "ua": "UA", "united": "UA", "united airlines": "UA",
    "dl": "DL", "delta": "DL", "delta air lines": "DL",
    "av": "AV", "avianca": "AV",
    "cm": "CM", "copa": "CM", "copa airlines": "CM",
    "lh": "LH", "lufthansa": "LH",
    "af": "AF", "air france": "AF",
    "kl": "KL", "klm": "KL",
}

MONTHS = {
    "enero": 1, "january": 1, "febrero": 2, "february": 2,
    "marzo": 3, "march": 3, "abril": 4, "april": 4,
    "mayo": 5, "may": 5, "junio": 6, "june": 6,
    "julio": 7, "july": 7, "agosto": 8, "august": 8,
    "septiembre": 9, "setiembre": 9, "september": 9,
    "octubre": 10, "october": 10, "noviembre": 11, "november": 11,
    "diciembre": 12, "december": 12,
}

NUMBER_WORDS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3,
    "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9,
}

ARGENTINA_AIRPORTS = {
    "EZE", "AEP", "BUE", "COR", "MDZ", "BRC", "NQN", "USH",
    "ROS", "SLA", "TUC", "IGR", "FTE",
}

UNSAFE_LOCATION_TOKENS = {
    "the", "and", "for", "con", "sin", "del", "por", "via",
    "ida", "dos", "una", "uno", "las", "los", "san",
    "ana",
}


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _contains_alias(text_folded: str, aliases: Iterable[str]) -> str | None:
    candidates = sorted(aliases, key=len, reverse=True)
    for alias in candidates:
        folded = _fold(alias)
        if re.search(rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])", text_folded):
            return alias
    return None



@lru_cache(maxsize=1)
def _reserved_airline_aliases() -> frozenset[str]:
    aliases = {
        _fold(alias).strip()
        for alias in CARRIER_ALIASES
        if len(_fold(alias).strip()) >= 3
    }

    try:
        repo = get_reference_repository()
        for rec in repo.alias_records("airline"):
            alias = _fold(str(rec["alias"])).strip()
            if len(alias) >= 3:
                aliases.add(alias)
    except Exception:
        pass

    return frozenset(aliases)


@lru_cache(maxsize=1)
def _location_reference_index() -> tuple[
    dict[str, tuple[tuple[str, str], ...]],
    tuple[str, ...],
]:
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    explicit_codes: set[str] = set()
    reserved_airline_aliases = _reserved_airline_aliases()

    for alias, code in AIRPORT_ALIASES.items():
        folded_alias = _fold(alias).strip()
        if len(alias) == 3 and alias.isalpha():
            explicit_codes.add(code)
            continue
        if folded_alias:
            index[folded_alias.split()[0]].append((folded_alias, code))

    try:
        repo = get_reference_repository()
        for entity_type in ("airport", "city"):
            for rec in repo.alias_records(entity_type):
                alias = str(rec["alias"]).strip()
                code = str(rec["code"]).upper().strip()
                if not alias or not code:
                    continue
                if alias.upper() == code and len(alias) == 3:
                    explicit_codes.add(code)
                    continue
                folded_alias = _fold(alias).strip()
                if not folded_alias:
                    continue

                if folded_alias in reserved_airline_aliases:
                    continue

                index[folded_alias.split()[0]].append((folded_alias, code))
    except Exception:
        pass

    normalized: dict[str, tuple[tuple[str, str], ...]] = {}
    for first_word, values in index.items():
        unique = list(dict.fromkeys(values))
        normalized[first_word] = tuple(
            sorted(unique, key=lambda item: len(item[0]), reverse=True)
        )

    return normalized, tuple(sorted(explicit_codes))


@lru_cache(maxsize=1)
def _airline_reference_index() -> tuple[
    dict[str, tuple[tuple[str, str], ...]],
    tuple[str, ...],
]:
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    explicit_codes: set[str] = {
        "AA", "AR", "LA", "G3", "IB", "UA", "DL", "AV", "CM", "LH", "AF", "KL"
    }

    for alias, code in CARRIER_ALIASES.items():
        if alias.upper() == code and len(alias) <= 3:
            explicit_codes.add(code)
            continue
        folded_alias = _fold(alias).strip()
        if len(folded_alias) >= 3:
            index[folded_alias.split()[0]].append((folded_alias, code))

    try:
        repo = get_reference_repository()
        for rec in repo.alias_records("airline"):
            alias = str(rec["alias"]).strip()
            code = str(rec["code"]).upper().strip()
            if not alias or not code:
                continue
            if alias.upper() == code and len(alias) <= 3:
                explicit_codes.add(code)
                continue
            folded_alias = _fold(alias).strip()
            if len(folded_alias) >= 3:
                index[folded_alias.split()[0]].append((folded_alias, code))
    except Exception:
        pass

    normalized: dict[str, tuple[tuple[str, str], ...]] = {}
    for first_word, values in index.items():
        unique = list(dict.fromkeys(values))
        normalized[first_word] = tuple(
            sorted(unique, key=lambda item: len(item[0]), reverse=True)
        )

    return normalized, tuple(sorted(explicit_codes))


def clear_reference_parser_caches() -> None:
    _reserved_airline_aliases.cache_clear()
    _location_reference_index.cache_clear()
    _airline_reference_index.cache_clear()


def _airport_occurrences(text: str) -> list[tuple[int, str]]:
    folded = _fold(text)
    found: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []
    temporal_phrases = (
        "manana",
        "madrugada",
        "mediodia",
        "tarde",
        "noche",
    )

    for phrase in temporal_phrases:
        for match in re.finditer(
            rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
            folded,
        ):
            occupied.append((match.start(), match.end()))

    for route_match in re.finditer(
        r"(?<![A-Za-z0-9])([A-Za-z]{3})\s*[-/]\s*([A-Za-z]{3})(?![A-Za-z0-9])",
        text,
    ):
        for group_index in (1, 2):
            token = route_match.group(group_index)
            if token.lower() in UNSAFE_LOCATION_TOKENS:
                continue
            found.append((route_match.start(group_index), token.upper()))
            occupied.append((route_match.start(group_index), route_match.end(group_index)))

    location_index, explicit_codes = _location_reference_index()
    words = set(re.findall(r"[a-z0-9]+", folded))

    candidates: list[tuple[str, str]] = []
    for word in words:
        candidates.extend(location_index.get(word, ()))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)

    for needle, code in candidates:
        if needle not in folded:
            continue
        for match in re.finditer(
            rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
            folded,
        ):
            if any(not (match.end() <= a or match.start() >= b) for a, b in occupied):
                continue
            found.append((match.start(), code))
            occupied.append((match.start(), match.end()))

    explicit_code_set = set(explicit_codes)
    token_matches = list(
        re.finditer(r"(?<![A-Za-z0-9])[A-Za-z]{3}(?![A-Za-z0-9])", text)
    )
    for token_match in token_matches:
        token = token_match.group(0)
        code = token.upper()
        if token.lower() in UNSAFE_LOCATION_TOKENS or code not in explicit_code_set:
            continue
        if any(not (token_match.end() <= a or token_match.start() >= b) for a, b in occupied):
            continue
        found.append((token_match.start(), code))
        occupied.append((token_match.start(), token_match.end()))

    found.sort()
    result: list[tuple[int, str]] = []
    for item in found:
        if result and result[-1][1] == item[1] and item[0] - result[-1][0] < 12:
            continue
        result.append(item)
    return result


def _resolve_buenos_aires(origin: str, destination: str) -> tuple[str, str, list[str]]:
    assumptions: list[str] = []
    if origin != "BUE" and destination != "BUE":
        return origin, destination, assumptions

    other = destination if origin == "BUE" else origin
    resolved = "AEP" if other in ARGENTINA_AIRPORTS else "EZE"
    if origin == "BUE":
        origin = resolved
    if destination == "BUE":
        destination = resolved

    assumptions.append(
        f"'Buenos Aires' interpretado como {resolved} "
        f"({'tramo doméstico argentino' if resolved == 'AEP' else 'tramo internacional'})."
    )
    return origin, destination, assumptions


def _number_value(token: str) -> int | None:
    token = _fold(token)
    if token.isdigit():
        return int(token)
    return NUMBER_WORDS.get(token)


def _resolve_year(month: int, day: int, explicit_year: int | None, today: date) -> int:
    if explicit_year:
        return explicit_year
    candidate = date(today.year, month, day)
    return today.year if candidate >= today else today.year + 1


def _parse_dates(text: str, today: date) -> tuple[date | None, date | None, list[str]]:
    folded = _fold(text)
    assumptions: list[str] = []

    iso = re.findall(r"\b(20\d{2})-(\d{2})-(\d{2})\b", folded)
    if iso:
        values = [date(int(y), int(m), int(d)) for y, m, d in iso[:2]]
        return values[0], values[1] if len(values) > 1 else None, assumptions

    numeric = re.findall(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b", folded)
    if numeric:
        values = []
        for d, m, y in numeric[:2]:
            year = _resolve_year(int(m), int(d), int(y) if y else None, today)
            if not y:
                assumptions.append(f"Año inferido para {int(d):02d}/{int(m):02d}: {year}.")
            values.append(date(year, int(m), int(d)))
        return values[0], values[1] if len(values) > 1 else None, assumptions

    month_names = "|".join(sorted((_fold(k) for k in MONTHS), key=len, reverse=True))

    range_match = re.search(
        rf"\b(\d{{1,2}})\s*(?:al|a|hasta|-)\s*(\d{{1,2}})\s+de\s+"
        rf"({month_names})(?:\s+(?:de\s+)?(20\d{{2}}))?",
        folded,
    )
    if range_match:
        d1, d2, month_name, year_text = range_match.groups()
        month = MONTHS[next(k for k in MONTHS if _fold(k) == month_name)]
        explicit_year = int(year_text) if year_text else None
        year = _resolve_year(month, int(d1), explicit_year, today)
        if explicit_year is None:
            assumptions.append(f"Año inferido: {year}.")
        return date(year, month, int(d1)), date(year, month, int(d2)), assumptions

    singles = list(re.finditer(
        rf"\b(\d{{1,2}})\s+de\s+({month_names})(?:\s+(?:de\s+)?(20\d{{2}}))?",
        folded,
    ))
    if singles:
        values = []
        for match in singles[:2]:
            d, month_name, year_text = match.groups()
            month = MONTHS[next(k for k in MONTHS if _fold(k) == month_name)]
            explicit_year = int(year_text) if year_text else None
            year = _resolve_year(month, int(d), explicit_year, today)
            if explicit_year is None:
                assumptions.append(f"Año inferido para {int(d)} de {month_name}: {year}.")
            values.append(date(year, month, int(d)))
        return values[0], values[1] if len(values) > 1 else None, assumptions

    return None, None, assumptions


def _location_spans_for_carrier_detection(folded: str) -> list[tuple[int, int]]:
    location_index, _explicit_codes = _location_reference_index()
    words = set(re.findall(r"[a-z0-9]+", folded))
    spans: list[tuple[int, int]] = []

    candidates: list[str] = []
    for word in words:
        candidates.extend(
            alias
            for alias, _code in location_index.get(word, ())
            if len(alias) >= 4
        )

    for alias in sorted(set(candidates), key=len, reverse=True):
        if alias not in folded:
            continue
        for match in re.finditer(
            rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
            folded,
        ):
            spans.append((match.start(), match.end()))

    return spans


def _carrier_sets(text: str) -> tuple[list[str], list[str]]:
    folded = _fold(text)
    words = set(re.findall(r"[a-z0-9]+", folded))

    excluded: set[str] = set()
    included: set[str] = set()

    exclusion_prefix = re.compile(
        r"(?:"
        r"excepto|menos|sin|excluir|exclui|excluye|"
        r"no\s+cotizar|no\s+cotices|no\s+incluir|no\s+incluyas?|"
        r"evitar|evita|evite"
        r")\s+[^,;]{0,32}$"
    )

    # Evita colisiones como Buenos Aires -> Aires -> 4C.
    location_spans = _location_spans_for_carrier_detection(folded)

    airline_index, explicit_codes_tuple = _airline_reference_index()
    explicit_codes = set(explicit_codes_tuple)

    candidates: list[tuple[str, str]] = []
    for word in words:
        candidates.extend(airline_index.get(word, ()))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)

    for needle, code in candidates:
        if len(needle) < 3 or needle not in folded:
            continue

        for match in re.finditer(
            rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
            folded,
        ):
            if any(
                match.start() >= start and match.end() <= end
                for start, end in location_spans
            ):
                continue

            before = folded[max(0, match.start() - 56):match.start()]
            if exclusion_prefix.search(before):
                excluded.add(code)
            else:
                included.add(code)

    # Sólo inspeccionamos tokens cortos realmente presentes en el prompt.
    explicit_tokens = {
        match.group(0)
        for match in re.finditer(
            r"(?<![A-Za-z0-9])[A-Za-z0-9]{2,3}(?![A-Za-z0-9])",
            text,
        )
    }

    for token in explicit_tokens:
        code = token.upper()

        if code not in explicit_codes:
            continue

        # Nunca aceptar designadores puramente numéricos:
        # "20 de diciembre" no puede ser una aerolínea.
        if not any(ch.isalpha() for ch in code):
            continue

        # Para códigos sólo alfabéticos mantenemos la regla de mayúsculas
        # explícitas: AA sí, "aa" no. Los alfanuméricos como G3 sí sirven.
        if code.isalpha() and token != token.upper():
            continue

        for match in re.finditer(
            rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
            text,
        ):
            before = _fold(text[max(0, match.start() - 56):match.start()])

            if exclusion_prefix.search(before):
                excluded.add(code)
            else:
                included.add(code)

    included -= excluded

    if re.search(
        r"\b(cualquier|cualquiera|todas?)\b.{0,24}"
        r"\b(aerolinea|aerolineas|compania|companias)\b",
        folded,
    ):
        included.clear()

    return sorted(included), sorted(excluded)


def _passengers(text: str) -> tuple[list[PassengerSpec], list[str]]:
    # Passenger parser v0.21.1.
    # Supported examples:
    # - 4 adultos / ADT x4 / 4 ADT
    # - 4 niños de 9 años / niños de 9 años x4 / C09 x4 / 4 C09
    # - 1 infante / INF x1 / 1 menor de 1 año
    # - niños de 9 y 7 años
    #
    # "menores de 11 años" is an upper bound, not an exact age.
    # To keep the workflow operational, use the highest compatible age
    # (C10 for <11) and emit an explicit warning.
    folded = _fold(text)
    warnings: list[str] = []

    number_token = (
        r"(?:\d+|un|uno|una|dos|tres|cuatro|cinco|"
        r"seis|siete|ocho|nueve)"
    )

    def qty_value(raw: str | None) -> int:
        if not raw:
            return 1
        return _number_value(raw) or 1

    # --------------------------------------------------------
    # Adults
    # --------------------------------------------------------
    adult_count = 1

    adult_match = re.search(
        rf"\b(?P<qty>{number_token})\s*"
        r"(?:adt|adulto|adultos|adult|adults)\b",
        folded,
    )
    if adult_match is None:
        adult_match = re.search(
            r"\b(?:adt|adulto|adultos|adult|adults)\b"
            rf"\s*(?:x|por)?\s*(?P<qty>{number_token})\b",
            folded,
        )

    if adult_match is not None:
        adult_count = qty_value(adult_match.group("qty"))
    else:
        people_match = re.search(
            rf"\b(?:para\s+)?(?P<qty>{number_token})\s+"
            r"(?:persona|personas|pasajero|pasajeros|pax)\b",
            folded,
        )
        if people_match is not None:
            adult_count = qty_value(people_match.group("qty"))

    groups: dict[tuple[PassengerKind, int | None], int] = {}
    occupied: list[tuple[int, int]] = []

    def overlaps(span: tuple[int, int]) -> bool:
        start_pos, end_pos = span
        return any(
            not (end_pos <= old_start or start_pos >= old_end)
            for old_start, old_end in occupied
        )

    def add_age_group(age: int, quantity: int, source: str) -> None:
        nonlocal adult_count

        if age >= 12:
            adult_count += quantity
            warning_subject = (
                f"{quantity} pasajero tratado como ADT"
                if quantity == 1
                else f"{quantity} pasajeros tratados como ADT"
            )
            warnings.append(
                f"{source}: {warning_subject} por tener 12 años o más."
            )
            return

        if age < 2:
            key = (PassengerKind.INFANT, age)
        else:
            key = (PassengerKind.CHILD, age)

        groups[key] = groups.get(key, 0) + quantity

    # --------------------------------------------------------
    # Explicit Sabre-style child PTCs: C09 x4 / 4 C09
    # --------------------------------------------------------
    child_ptc_patterns = [
        rf"\b(?P<qty>{number_token})\s*c(?P<age>\d{{1,2}})\b",
        rf"\bc(?P<age>\d{{1,2}})\b"
        rf"\s*(?:x|por)?\s*(?P<qty>{number_token})\b",
    ]

    for pattern in child_ptc_patterns:
        for match in re.finditer(pattern, folded):
            if overlaps(match.span()):
                continue

            age = int(match.group("age"))
            quantity = qty_value(match.group("qty"))

            if age < 2:
                raise ValueError(
                    f"C{age:02d} no es un PTC CHILD válido en este agente. "
                    "Para menores de 2 años usá INF."
                )

            add_age_group(age, quantity, f"C{age:02d}")
            occupied.append(match.span())

    # --------------------------------------------------------
    # Upper-bound wording:
    #   niños menores de 11 años x4 -> C10 x4 + warning
    #   1 menor de 1 año            -> INF x1 + warning
    # --------------------------------------------------------
    threshold_patterns = [
        rf"\b(?P<qty>{number_token})\s+"
        r"(?:(?:nino|ninos|nina|ninas|chico|chicos|chica|chicas)\s+)?"
        r"(?:menor|menores)\s+de\s+(?P<limit>\d{1,2})"
        r"\s*(?:anos?|ano)?\b",
        r"\b(?:(?:nino|ninos|nina|ninas|chico|chicos|chica|chicas)\s+)?"
        r"(?:menor|menores)\s+de\s+(?P<limit>\d{1,2})"
        rf"\s*(?:anos?|ano)?\b\s*(?:x|por)\s*(?P<qty>{number_token})\b",
    ]

    for pattern in threshold_patterns:
        for match in re.finditer(pattern, folded):
            if overlaps(match.span()):
                continue

            limit = int(match.group("limit"))
            quantity = qty_value(match.group("qty"))

            if limit <= 0:
                raise ValueError(
                    f"'menor de {limit} años' no define una edad válida."
                )

            assumed_age = max(0, limit - 1)
            add_age_group(
                assumed_age,
                quantity,
                f"Menor de {limit} años",
            )
            warnings.append(
                f"'menor de {limit} años' ×{quantity} interpretado con "
                f"edad máxima {assumed_age} para determinar el PTC. "
                "Si las edades reales difieren, indicarlas individualmente."
            )
            occupied.append(match.span())

    # --------------------------------------------------------
    # Age lists: niños de 9 y 7 años / niños de 9, 7 y 5 años
    # --------------------------------------------------------
    age_list_pattern = (
        r"\b(?:ninos|ninas|chicos|chicas)\s+de\s+"
        r"(?P<ages>\d{1,2}(?:\s*(?:,|y)\s*\d{1,2})+)"
        r"\s*(?:anos?|ano)?\b"
    )
    for match in re.finditer(age_list_pattern, folded):
        if overlaps(match.span()):
            continue

        ages = [
            int(value)
            for value in re.findall(r"\d{1,2}", match.group("ages"))
        ]
        for age in ages:
            add_age_group(age, 1, f"Edad {age}")

        occupied.append(match.span())

    # --------------------------------------------------------
    # Exact child age with quantity:
    #   4 niños de 9 años
    #   niños de 9 años x4
    # --------------------------------------------------------
    exact_child_patterns = [
        rf"\b(?P<qty>{number_token})\s+"
        r"(?:nino|ninos|nina|ninas|chico|chicos|chica|chicas|child|children)"
        r"\s+(?:de\s+)?(?P<age>\d{1,2})"
        r"\s*(?:anos?|ano)?\b",
        r"\b(?:nino|ninos|nina|ninas|chico|chicos|chica|chicas|child|children)"
        r"\s+(?:de\s+)?(?P<age>\d{1,2})"
        rf"\s*(?:anos?|ano)?\b\s*(?:x|por)\s*(?P<qty>{number_token})\b",
    ]

    for pattern in exact_child_patterns:
        for match in re.finditer(pattern, folded):
            if overlaps(match.span()):
                continue

            add_age_group(
                int(match.group("age")),
                qty_value(match.group("qty")),
                f"Edad {match.group('age')}",
            )
            occupied.append(match.span())

    # Single explicit age, preserving older conversational syntax.
    single_age_pattern = (
        r"\b(?:nino|nina|chico|chica|otro|otra)\s+"
        r"(?:de\s+)?(?P<age>\d{1,2})"
        r"\s*(?:anos?|ano)?\b"
    )
    for match in re.finditer(single_age_pattern, folded):
        if overlaps(match.span()):
            continue

        add_age_group(
            int(match.group("age")),
            1,
            f"Edad {match.group('age')}",
        )
        occupied.append(match.span())

    # --------------------------------------------------------
    # Explicit infants: 2 infantes / INF x2
    # --------------------------------------------------------
    infant_patterns = [
        rf"\b(?P<qty>{number_token})\s+"
        r"(?:inf|infante|infantes|bebe|bebes|infant|infants)\b",
        r"\b(?:inf|infante|infantes|bebe|bebes|infant|infants)\b"
        rf"\s*(?:x|por)?\s*(?P<qty>{number_token})\b",
    ]

    for pattern in infant_patterns:
        for match in re.finditer(pattern, folded):
            if overlaps(match.span()):
                continue

            key = (PassengerKind.INFANT, None)
            groups[key] = groups.get(key, 0) + qty_value(match.group("qty"))
            occupied.append(match.span())

    # If children were explicitly counted but no age/PTC could be inferred,
    # fail instead of silently pricing them as adults.
    generic_child_match = re.search(
        rf"\b(?P<qty>{number_token})\s+"
        r"(?:nino|ninos|nina|ninas|chico|chicos|chica|chicas|"
        r"menor|menores|child|children)\b",
        folded,
    )

    has_minor_group = any(
        kind in {PassengerKind.CHILD, PassengerKind.INFANT}
        for kind, _age in groups
    )

    if (
        generic_child_match is not None
        and not has_minor_group
        and not overlaps(generic_child_match.span())
    ):
        quantity = qty_value(generic_child_match.group("qty"))
        raise ValueError(
            f"Se detectaron {quantity} menor(es) sin edad. "
            "Necesito la edad de cada menor "
            "(por ejemplo C09 x4 o '4 niños de 9 años')."
        )

    passengers: list[PassengerSpec] = [
        PassengerSpec(
            type=PassengerKind.ADULT,
            quantity=adult_count,
        )
    ]

    for (kind, age), quantity in groups.items():
        passengers.append(
            PassengerSpec(
                type=kind,
                age=age,
                quantity=quantity,
            )
        )

    return passengers, warnings

_MONTH_ABBR = {
    # English / Sabre-style
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
    # Spanish common abbreviations
    "ENE": 1,
    "ABR": 4,
    "AGO": 8,
    "SET": 9,
    "DIC": 12,
}


def _parse_compact_date_token(token: str, today: date) -> date | None:
    value = token.strip().upper()

    match = re.fullmatch(
        r"(\d{1,2})\s*([A-Z]{3})(?:\s*(20\d{2}))?",
        value,
    )
    if match:
        day_text, month_text, year_text = match.groups()
        month = _MONTH_ABBR.get(month_text)
        if month is None:
            return None
        day = int(day_text)
        year = _resolve_year(
            month,
            day,
            int(year_text) if year_text else None,
            today,
        )
        return date(year, month, day)

    match = re.fullmatch(
        r"(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?",
        value,
    )
    if match:
        day_text, month_text, year_text = match.groups()
        day = int(day_text)
        month = int(month_text)
        year = _resolve_year(
            month,
            day,
            int(year_text) if year_text else None,
            today,
        )
        return date(year, month, day)

    return None


def _parse_compact_dates_in_text(
    text: str,
    today: date,
) -> list[date]:
    values: list[date] = []

    for match in re.finditer(
        r"\b("
        r"\d{1,2}\s*[A-Za-z]{3}(?:\s*20\d{2})?"
        r"|"
        r"\d{1,2}[/-]\d{1,2}(?:[/-]20\d{2})?"
        r")\b",
        text,
    ):
        parsed = _parse_compact_date_token(
            match.group(1),
            today,
        )
        if parsed is not None:
            values.append(parsed)

    return values


def _explicit_route_occurrences(text: str) -> list[tuple[int, int, str, str]]:
    return [
        (
            match.start(),
            match.end(),
            match.group(1).upper(),
            match.group(2).upper(),
        )
        for match in re.finditer(
            r"(?<![A-Za-z0-9])"
            r"([A-Za-z]{3})\s*[-/]\s*([A-Za-z]{3})"
            r"(?![A-Za-z0-9])",
            text,
        )
    ]


def _parse_explicit_trip_legs(
    text: str,
    today: date,
) -> list[SearchLeg]:
    routes = _explicit_route_occurrences(text)
    if not routes:
        return []

    legs: list[SearchLeg] = []

    for index, (_start, end, origin, destination) in enumerate(routes):
        next_start = (
            routes[index + 1][0]
            if index + 1 < len(routes)
            else len(text)
        )
        tail = text[end:next_start]

        date_match = re.search(
            r"\b("
            r"\d{1,2}\s*[A-Za-z]{3}(?:\s*20\d{2})?"
            r"|"
            r"\d{1,2}[/-]\d{1,2}(?:[/-]20\d{2})?"
            r")\b",
            tail,
        )

        if not date_match:
            if len(routes) == 1:
                return []
            raise ValueError(
                f"No pude identificar la fecha del tramo "
                f"{origin}-{destination}."
            )

        leg_date = _parse_compact_date_token(
            date_match.group(1),
            today,
        )
        if leg_date is None:
            raise ValueError(
                f"No pude interpretar la fecha del tramo "
                f"{origin}-{destination}."
            )

        legs.append(
            SearchLeg(
                origin=origin,
                destination=destination,
                departure_date=leg_date,
            )
        )

    dates = [leg.departure_date for leg in legs]
    if dates != sorted(dates):
        raise ValueError(
            "Las fechas de los tramos deben estar en orden cronológico."
        )

    return legs


def _return_origin_after_phrase(
    text: str,
    airports: list[tuple[int, str]],
) -> str | None:
    folded = _fold(text)
    prefix = re.search(
        r"\b(?:regreso|vuelta|return)\s+desde\b",
        folded,
    )
    if not prefix:
        return None

    for position, code in airports:
        if position >= prefix.end():
            return code

    return None


def _canonical_trip_legs(
    *,
    text: str,
    today: date,
    airports: list[tuple[int, str]],
    origin: str,
    destination: str,
    departure: date,
    return_date: date | None,
) -> tuple[list[SearchLeg], TripType]:
    explicit = _parse_explicit_trip_legs(text, today)

    if len(explicit) >= 2:
        return explicit, infer_trip_type(explicit)

    if return_date is None:
        legs = [
            SearchLeg(
                origin=origin,
                destination=destination,
                departure_date=departure,
            )
        ]
        return legs, TripType.ONE_WAY

    return_origin = _return_origin_after_phrase(
        text,
        airports,
    )

    if return_origin and return_origin != destination:
        legs = [
            SearchLeg(
                origin=origin,
                destination=destination,
                departure_date=departure,
            ),
            SearchLeg(
                origin=return_origin,
                destination=origin,
                departure_date=return_date,
            ),
        ]
        return legs, infer_trip_type(legs)

    legs = [
        SearchLeg(
            origin=origin,
            destination=destination,
            departure_date=departure,
        ),
        SearchLeg(
            origin=destination,
            destination=origin,
            departure_date=return_date,
        ),
    ]
    return legs, TripType.ROUND_TRIP



def parse_agent_quote(
    request: AgentQuoteRequest,
    *,
    today: date | None = None,
) -> AgentInterpretation:
    today = today or date.today()
    text = request.text.strip()
    folded = _fold(text)

    assumptions: list[str] = []
    warnings: list[str] = []

    airports = _airport_occurrences(text)
    if len(airports) < 2:
        raise ValueError("No pude identificar con certeza origen y destino.")

    origin = airports[0][1]
    destination = airports[1][1]

    # Arrival-led phrasing mentions destination before origin.
    if re.search(r"\blleg(?:uen|ar|ando)?\s+a\b.*?\bdesde\b", folded):
        origin, destination = destination, origin

    departure, return_date, date_assumptions = _parse_dates(text, today)
    assumptions.extend(date_assumptions)

    compact_dates = _parse_compact_dates_in_text(text, today)
    departure_was_already_known = departure is not None

    if departure is None and compact_dates:
        departure = compact_dates[0]

    if return_date is None:
        if len(compact_dates) >= 2:
            return_date = compact_dates[1]
        elif (
            departure_was_already_known
            and compact_dates
            and compact_dates[0] != departure
        ):
            return_date = compact_dates[0]

    time_constraints, inferred_departure, inferred_return, time_assumptions = parse_time_constraints(
        text,
        today=today,
    )
    assumptions.extend(time_assumptions)

    if inferred_departure is not None:
        departure = inferred_departure
    if inferred_return is not None:
        return_date = inferred_return

    explicit_legs = _parse_explicit_trip_legs(text, today)
    if len(explicit_legs) >= 2:
        departure = explicit_legs[0].departure_date
        if len(explicit_legs) == 2:
            return_date = explicit_legs[1].departure_date
        elif len(explicit_legs) >= 3:
            return_date = None

    if departure is None:
        raise ValueError("No pude identificar la fecha de salida.")

    legs, trip_type = _canonical_trip_legs(
        text=text,
        today=today,
        airports=airports,
        origin=origin,
        destination=destination,
        departure=departure,
        return_date=return_date,
    )

    origin = legs[0].origin
    destination = legs[0].destination
    departure = legs[0].departure_date

    if len(legs) == 2:
        return_date = legs[1].departure_date
    elif len(legs) >= 3:
        return_date = None

    carriers, excluded = _carrier_sets(text)
    passengers, passenger_warnings = _passengers(text)
    warnings.extend(passenger_warnings)

    direct = bool(re.search(
        r"\b(directo|directos|directa|directas|nonstop|non-stop|"
        r"sin escalas?|sin conexiones?|vuelo directo|vuelos directos)\b",
        folded,
    ))

    if re.search(
        r"\b(ambas monedas|usd\s+y\s+ars|ars\s+y\s+usd)\b",
        folded,
    ):
        currency = PricingCurrency.BOTH
    elif re.search(r"\b(ars|pesos?|mars)\b", folded):
        currency = PricingCurrency.ARS
    elif re.search(r"\b(usd|dolares?|dollars?|musd)\b", folded):
        currency = PricingCurrency.USD
    else:
        currency = PricingCurrency.AUTO
        assumptions.append(
            "Moneda AUTO: USD internacional / ARS doméstico Argentina."
        )

    if re.search(
        r"\b(con devolucion|con reembolso|devolucion permitida|"
        r"devoluciones permitidas|reembolso permitido|reembolsable|refundable)\b",
        folded,
    ):
        fare_preference = FarePreference.REFUNDABLE
    elif re.search(
        r"\b(con valija|con valijas|con equipaje|con equipaje despachado|"
        r"incluya equipaje|incluya valija|incluya valijas|baggage)\b",
        folded,
    ):
        fare_preference = FarePreference.BAGGAGE
    elif re.search(
        r"\b(branded|familias tarifarias|marcas tarifarias)\b",
        folded,
    ):
        fare_preference = FarePreference.BRANDED
    else:
        fare_preference = FarePreference.AUTO

    cabin_patterns = [
        (Cabin.FIRST, r"\b(first|primera clase)\b"),
        (
            Cabin.PREMIUM_ECONOMY,
            r"\b(premium economy|premium econom[yia]|economy premium)\b",
        ),
        (Cabin.BUSINESS, r"\b(business|ejecutiva)\b"),
        (Cabin.ECONOMY, r"\b(economy|economica|economy class)\b"),
    ]

    mentioned_cabins: list[Cabin] = []

    premium_pattern = r"\b(premium economy|premium econom[yia]|economy premium)\b"
    folded_without_premium = re.sub(premium_pattern, " ", folded)

    for candidate, pattern in cabin_patterns:
        haystack = (
            folded_without_premium
            if candidate == Cabin.ECONOMY
            else folded
        )

        if re.search(pattern, haystack) and candidate not in mentioned_cabins:
            mentioned_cabins.append(candidate)

    def _cabin_after(prefix_pattern: str) -> Cabin | None:
        for candidate, pattern in cabin_patterns:
            if re.search(
                rf"\b(?:{prefix_pattern})\b[^,;.]*?{pattern}",
                folded,
            ):
                return candidate
        return None

    outbound_cabin = _cabin_after(r"ida|salida|outbound")
    return_cabin = _cabin_after(r"vuelta|regreso|return")

    if (
        outbound_cabin
        and return_cabin
        and outbound_cabin != return_cabin
    ):
        cabins = [outbound_cabin, return_cabin]
        cabin = outbound_cabin
        warnings.append(
            "Se detectaron cabinas distintas por tramo "
            f"(ida {outbound_cabin.value}, vuelta {return_cabin.value}). "
            "BFM CabinPref es global en la implementación actual; no se ejecutó una "
            "cotización mixta para evitar mostrar una tarifa incorrecta."
        )
    elif mentioned_cabins:
        commercial_order = [
            Cabin.ECONOMY,
            Cabin.PREMIUM_ECONOMY,
            Cabin.BUSINESS,
            Cabin.FIRST,
        ]
        cabins = [
            item
            for item in commercial_order
            if item in mentioned_cabins
        ]
        cabin = cabins[0]
    else:
        cabins = [
            Cabin.ECONOMY,
            Cabin.PREMIUM_ECONOMY,
            Cabin.BUSINESS,
        ]
        cabin = Cabin.ECONOMY
        assumptions.append(
            "No se indicó cabina: se cotizan Economy, Premium Economy y Business."
        )

    max_options = request.max_options or 5

    option_match = re.search(
        r"\b(?:mostra(?:me)?|pasame|dame)?\s*(\d+)\s+"
        r"(?:opciones|alternativas)\b",
        folded,
    )
    if option_match:
        max_options = min(50, max(1, int(option_match.group(1))))

    business_companion = False

    argentina_domestic = all(
        leg.origin in ARGENTINA_AIRPORTS
        and leg.destination in ARGENTINA_AIRPORTS
        for leg in legs
    )

    if argentina_domestic:
        if currency in {PricingCurrency.USD, PricingCurrency.BOTH}:
            warnings.append(
                "Se solicitó otra moneda, pero los vuelos domésticos dentro de Argentina "
                "se cotizan obligatoriamente en ARS."
            )
        else:
            assumptions.append(
                "Vuelo doméstico Argentina: moneda ARS obligatoria."
            )
        currency = PricingCurrency.ARS

    confidence = 0.55
    confidence += 0.15 if len(airports) >= 2 else 0
    confidence += 0.15 if departure else 0
    confidence += 0.05 if return_date else 0
    confidence += 0.05 if direct else 0
    confidence += 0.05 if carriers or excluded else 0
    confidence = min(confidence, 0.98)

    search_request = QuoteSearchAPIRequest(
        environment=request.environment,
        origin=origin,
        destination=destination,
        departure_date=departure,
        return_date=return_date,
        trip_type=trip_type,
        legs=legs,
        passengers=passengers,
        adults=sum(
            p.quantity
            for p in passengers
            if p.type == PassengerKind.ADULT
        ),
        children=sum(
            p.quantity
            for p in passengers
            if p.type == PassengerKind.CHILD
        ),
        infants=sum(
            p.quantity
            for p in passengers
            if p.type == PassengerKind.INFANT
        ),
        cabin=cabin,
        cabins=cabins,
        outbound_cabin=outbound_cabin,
        return_cabin=return_cabin,
        direct=direct,
        max_stops=0 if direct else 1,
        max_options=max_options,
        currency=currency,
        carriers=carriers,
        excluded_carriers=excluded,
        fare_preference=fare_preference,
        business_companion=business_companion,
        time_constraints=time_constraints,
    )

    return AgentInterpretation(
        confidence=confidence,
        assumptions=assumptions,
        warnings=warnings,
        search_request=search_request,
    )