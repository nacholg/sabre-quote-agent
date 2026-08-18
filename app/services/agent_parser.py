from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date
from functools import lru_cache
from typing import Iterable

from app.models.api import AgentInterpretation, AgentQuoteRequest, QuoteSearchAPIRequest
from app.models.quote_request import Cabin, FarePreference, PassengerKind, PassengerSpec
from app.services.pricing_rules import PricingCurrency
from app.services.reference_repository import get_reference_repository
from app.services.time_parser import parse_time_constraints


AIRPORT_ALIASES = {
    "eze": "EZE", "ezeiza": "EZE",
    "aep": "AEP", "aeroparque": "AEP",
    "bue": "BUE", "buenos aires": "BUE",
    "mia": "MIA", "miami": "MIA",
    "jfk": "JFK", "nueva york": "JFK", "new york": "JFK", "nyc": "JFK",
    "dfw": "DFW", "dallas": "DFW",
    "mad": "MAD", "madrid": "MAD",
    "bcn": "BCN", "barcelona": "BCN",
    "lhr": "LHR", "londres": "LHR", "london": "LHR",
    "cdg": "CDG", "paris": "CDG", "parís": "CDG",
    "gru": "GRU", "sao paulo": "GRU", "são paulo": "GRU",
    "gig": "GIG", "rio": "GIG", "rio de janeiro": "GIG",
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
    folded = _fold(text)
    warnings: list[str] = []

    number = r"(\d+|un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)"

    adults = 1
    adult_match = re.search(
        rf"\b{number}\s+(adulto|adultos|adult|adults)\b",
        folded,
    )

    if adult_match:
        adults = _number_value(adult_match.group(1)) or 1
    else:
        people_match = re.search(
            rf"\b(?:para\s+)?{number}\s+"
            r"(persona|personas|pasajero|pasajeros|pax)\b",
            folded,
        )
        if people_match:
            adults = _number_value(people_match.group(1)) or 1

    passengers: list[PassengerSpec] = [
        PassengerSpec(type=PassengerKind.ADULT, quantity=adults)
    ]

    age_hits: list[tuple[int, int]] = []

    for match in re.finditer(
        r"\b(?:nino|nina|ninos|ninas|chico|chica|chicos|chicas|menor|menores)"
        r"\s+(?:de\s+)?(\d{1,2})\b",
        folded,
    ):
        age_hits.append((match.start(1), int(match.group(1))))

    for match in re.finditer(
        r"\b(?:ninos|ninas|chicos|chicas|menores)\s+de\s+"
        r"\d{1,2}\s+y\s+(\d{1,2})\b",
        folded,
    ):
        age_hits.append((match.start(1), int(match.group(1))))

    for match in re.finditer(
        r"\b(?:otro|otra)\s+de\s+(\d{1,2})\b",
        folded,
    ):
        age_hits.append((match.start(1), int(match.group(1))))

    child_ages = [
        age for _position, age in sorted(dict(age_hits).items())
    ]

    for age in child_ages:
        if 2 <= age <= 11:
            passengers.append(
                PassengerSpec(
                    type=PassengerKind.CHILD,
                    age=age,
                    quantity=1,
                )
            )
        elif age >= 12:
            passengers[0].quantity += 1
            warnings.append(
                f"Pasajero de {age} años tratado como ADT por tener 12 años o más."
            )
        else:
            passengers.append(
                PassengerSpec(
                    type=PassengerKind.INFANT,
                    age=age,
                    quantity=1,
                )
            )
            warnings.append(
                f"Pasajero de {age} año(s) tratado como INF; se asume sin asiento."
            )

    generic_child_match = re.search(
        rf"\b{number}\s+"
        r"(nino|ninos|nina|ninas|chico|chicos|chica|chicas|"
        r"menor|menores|child|children)\b",
        folded,
    )

    if generic_child_match and not child_ages:
        qty = _number_value(generic_child_match.group(1)) or 1
        raise ValueError(
            f"Se detectaron {qty} menor(es) sin edad. "
            "Necesito la edad de cada menor para determinar el PTC Cxx."
        )

    infant_match = re.search(
        rf"\b{number}\s+(infante|infantes|bebe|bebes|infant|infants)\b",
        folded,
    )

    if infant_match:
        qty = _number_value(infant_match.group(1)) or 1
        passengers.append(
            PassengerSpec(
                type=PassengerKind.INFANT,
                quantity=qty,
            )
        )

    return passengers, warnings


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

    origin, destination, bue_assumptions = _resolve_buenos_aires(
        origin,
        destination,
    )
    assumptions.extend(bue_assumptions)

    departure, return_date, date_assumptions = _parse_dates(text, today)
    assumptions.extend(date_assumptions)

    time_constraints, inferred_departure, inferred_return, time_assumptions = parse_time_constraints(
        text,
        today=today,
    )
    assumptions.extend(time_assumptions)

    if inferred_departure is not None:
        departure = inferred_departure
    if inferred_return is not None:
        return_date = inferred_return

    if departure is None:
        raise ValueError("No pude identificar la fecha de salida.")

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

    argentina_domestic = (
        origin in ARGENTINA_AIRPORTS
        and destination in ARGENTINA_AIRPORTS
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

    if len(airports) > 2:
        warnings.append(
            "Detecté más de dos aeropuertos; esta versión interpreta los dos primeros "
            "como origen/destino. Para open jaw/circle trip conviene usar "
            "/quotes/search estructurado por ahora."
        )

    search_request = QuoteSearchAPIRequest(
        environment=request.environment,
        origin=origin,
        destination=destination,
        departure_date=departure,
        return_date=return_date,
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