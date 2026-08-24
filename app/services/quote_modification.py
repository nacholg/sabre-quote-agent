from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any

from app.models.api import (
    QuoteChangeItem,
    QuoteModificationRequest,
    QuoteModificationResponse,
    QuoteSearchAPIRequest,
)
from app.models.quote_request import (
    Cabin,
    FarePreference,
    PassengerKind,
    PassengerSpec,
)
from app.services.agent_parser import _carrier_sets, _parse_dates
from app.services.quote_repository import QuoteRepository
from app.services.quote_service import search_quote


_ADULT_COUNT_PATTERNS = (
    re.compile(
        r"\b(?:cotiz(?:ar|ame)?\s+)?(?:para\s+)?"
        r"(?P<count>[1-9])\s+"
        r"(?:personas?|pasajeros?|adultos?|adt)\b"
    ),
    re.compile(
        r"\b(?:somos|seriamos|serian)\s+"
        r"(?P<count>[1-9])\b"
    ),
)

_CABIN_PATTERNS: tuple[tuple[Cabin, re.Pattern[str]], ...] = (
    (
        Cabin.PREMIUM_ECONOMY,
        re.compile(r"\b(?:premium\s+economy|economy\s+premium|premium)\b"),
    ),
    (
        Cabin.BUSINESS,
        re.compile(r"\b(?:business|ejecutiva|ejecutivo)\b"),
    ),
    (
        Cabin.FIRST,
        re.compile(r"\b(?:first|primera\s+clase)\b"),
    ),
    (
        Cabin.ECONOMY,
        re.compile(r"\b(?:economy|economica|economico|turista)\b"),
    ),
)

_DIRECT_PATTERN = re.compile(
    r"\b(?:solo\s+directos?|sin\s+escalas?|non-?stop)\b"
)
_ONE_STOP_PATTERN = re.compile(
    r"\b(?:permiti(?:r)?|acepta(?:r)?|con)\s+"
    r"(?:una|1)\s+escala\b"
)

_RETURN_CONTEXT = re.compile(
    r"\b(?:volv\w*|regres\w*|vuelta|retorno|return)\b"
)
_DEPARTURE_CONTEXT = re.compile(
    r"\b(?:salir|salida|sali|partir|partida|ida|departure)\b"
)

_RELATIVE_DATE_PATTERN = re.compile(
    r"\b(?:(?P<count>"
    r"\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete"
    r")\s+)?dias?\s+"
    r"(?P<direction>antes|despues)\b"
)

_ORDINAL_REPLACEMENTS = {
    "primero": "1",
    "primer": "1",
    "primera": "1",
    "segundo": "2",
    "segunda": "2",
    "tercero": "3",
    "tercer": "3",
    "tercera": "3",
}

_NUMBER_WORDS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
}

_CLEAR_INCLUDED_CARRIERS = re.compile(
    r"\b(?:cualquier|cualquiera|todas?)\s+"
    r"(?:aerolinea|aerolineas|compania|companias)\b"
    r"|"
    r"\bsin\s+(?:preferencia|restriccion)\s+"
    r"(?:de\s+)?(?:aerolinea|aerolineas)\b"
)
_CLEAR_EXCLUDED_CARRIERS = re.compile(
    r"\b(?:sin\s+exclusiones?|quitar\s+exclusiones?|"
    r"limpiar\s+exclusiones?)\b"
)
_ADD_CARRIER_CONTEXT = re.compile(
    r"\b(?:tambien|agrega|agregar|sumar|suma)\b"
)

_FARE_PATTERNS: tuple[tuple[FarePreference, re.Pattern[str]], ...] = (
    (
        FarePreference.REFUNDABLE,
        re.compile(
            r"\b(?:refundable|reembolsable|con\s+devolucion|"
            r"con\s+reembolso)\b"
        ),
    ),
    (
        FarePreference.BAGGAGE,
        re.compile(
            r"\b(?:con\s+equipaje|con\s+valija|con\s+valijas|"
            r"equipaje\s+despachado|baggage)\b"
        ),
    ),
    (
        FarePreference.BRANDED,
        re.compile(
            r"\b(?:branded|familias\s+tarifarias|marcas\s+tarifarias)\b"
        ),
    ),
    (
        FarePreference.LOWEST,
        re.compile(
            r"\b(?:lowest|mas\s+barata|menor\s+tarifa|"
            r"precio\s+mas\s+bajo|tarifa\s+mas\s+economica)\b"
        ),
    ),
    (
        FarePreference.AUTO,
        re.compile(
            r"\b(?:tarifa\s+auto|automatico|automatica|"
            r"sin\s+preferencia\s+tarifaria)\b"
        ),
    ),
)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    return "".join(
        ch
        for ch in value
        if unicodedata.category(ch) != "Mn"
    )


def _passenger_label(request: QuoteSearchAPIRequest) -> str:
    parts: list[str] = []

    if request.passengers:
        for passenger in request.passengers:
            if passenger.type == PassengerKind.CHILD:
                code = (
                    f"C{passenger.age:02d}"
                    if passenger.age is not None
                    else "CHILD"
                )
            else:
                code = passenger.type.value
            parts.append(f"{passenger.quantity} {code}")
    else:
        parts.append(f"{request.adults} ADT")
        if request.children:
            parts.append(f"{request.children} C{request.child_age:02d}")
        if request.infants:
            parts.append(f"{request.infants} INF")

    return " + ".join(parts)


def _replace_adults(
    request_data: dict[str, Any],
    quantity: int,
) -> None:
    passengers = list(request_data.get("passengers") or [])

    if passengers:
        updated: list[dict[str, Any]] = []
        adult_seen = False

        for raw in passengers:
            passenger = PassengerSpec.model_validate(raw)
            if passenger.type == PassengerKind.ADULT:
                if not adult_seen:
                    updated.append(
                        PassengerSpec(
                            type=PassengerKind.ADULT,
                            quantity=quantity,
                        ).model_dump(mode="json")
                    )
                    adult_seen = True
                continue

            updated.append(passenger.model_dump(mode="json"))

        if not adult_seen:
            updated.insert(
                0,
                PassengerSpec(
                    type=PassengerKind.ADULT,
                    quantity=quantity,
                ).model_dump(mode="json"),
            )

        request_data["passengers"] = updated

    request_data["adults"] = quantity


def _normalize_ordinal_dates(text: str) -> str:
    folded = _fold(text)
    for word, number in _ORDINAL_REPLACEMENTS.items():
        folded = re.sub(
            rf"\b{word}\b",
            number,
            folded,
        )
    return folded


def _base_departure_date(
    request: QuoteSearchAPIRequest,
) -> date:
    if request.legs:
        return request.legs[0].departure_date
    assert request.departure_date is not None
    return request.departure_date


def _base_return_date(
    request: QuoteSearchAPIRequest,
) -> date | None:
    if len(request.legs) > 2:
        return None
    if len(request.legs) == 2:
        return request.legs[1].departure_date
    return request.return_date


def _date_role(
    folded: str,
    base: QuoteSearchAPIRequest,
) -> str:
    has_return_context = bool(_RETURN_CONTEXT.search(folded))
    has_departure_context = bool(
        _DEPARTURE_CONTEXT.search(folded)
    )

    if has_return_context and has_departure_context:
        raise ValueError(
            "El mensaje mezcla salida y regreso con una sola fecha. "
            "Indicá cada cambio por separado o escribí ambas fechas."
        )

    if has_return_context:
        if len(base.legs) > 2:
            raise ValueError(
                "Para un itinerario multitramos indicá qué tramo querés "
                "modificar; no voy a asumir cuál es la vuelta."
            )
        if _base_return_date(base) is None:
            raise ValueError(
                "La cotización actual no tiene regreso. "
                "Todavía no agrego un tramo nuevo de vuelta "
                "desde una modificación conversacional."
            )
        return "return_date"

    if has_departure_context:
        return "departure_date"

    if _base_return_date(base) is not None:
        raise ValueError(
            "Detecté una fecha nueva, pero necesito saber si querés "
            "cambiar la salida o el regreso."
        )

    return "departure_date"


def _date_delta(
    text: str,
    base: QuoteSearchAPIRequest,
) -> dict[str, date]:
    folded = _fold(text)
    normalized = _normalize_ordinal_dates(text)

    relative = _RELATIVE_DATE_PATTERN.search(folded)
    if relative:
        role = _date_role(folded, base)
        raw_count = relative.group("count") or "1"
        count = (
            int(raw_count)
            if raw_count.isdigit()
            else _NUMBER_WORDS.get(raw_count, 1)
        )
        direction = relative.group("direction")
        sign = -1 if direction == "antes" else 1

        current = (
            _base_departure_date(base)
            if role == "departure_date"
            else _base_return_date(base)
        )
        assert current is not None
        return {
            role: current + timedelta(days=sign * count)
        }

    parsed_departure, parsed_return, _ = _parse_dates(
        normalized,
        date.today(),
    )

    if parsed_departure is None:
        return {}

    if parsed_return is not None:
        if len(base.legs) > 2:
            raise ValueError(
                "La modificación de dos fechas todavía está limitada "
                "a ida y vuelta simples."
            )
        if _base_return_date(base) is None:
            raise ValueError(
                "La cotización actual es sólo ida. "
                "No voy a agregar automáticamente un tramo de regreso."
            )
        return {
            "departure_date": parsed_departure,
            "return_date": parsed_return,
        }

    role = _date_role(folded, base)
    return {role: parsed_departure}


def _interpret_delta(
    text: str,
    base: QuoteSearchAPIRequest,
) -> dict[str, Any]:
    folded = _fold(text)
    delta: dict[str, Any] = {}

    for pattern in _ADULT_COUNT_PATTERNS:
        match = pattern.search(folded)
        if match:
            delta["adults"] = int(match.group("count"))
            break

    lowest_fare_requested = bool(
        _FARE_PATTERNS[3][1].search(folded)
    )

    for cabin, pattern in _CABIN_PATTERNS:
        if lowest_fare_requested and cabin == Cabin.ECONOMY:
            if "economy" not in folded and "turista" not in folded:
                continue
        if pattern.search(folded):
            delta["cabin"] = cabin
            break

    if _DIRECT_PATTERN.search(folded):
        delta["direct"] = True
        delta["max_stops"] = 0
    elif _ONE_STOP_PATTERN.search(folded):
        delta["direct"] = False
        delta["max_stops"] = 1

    delta.update(_date_delta(text, base))

    included, excluded = _carrier_sets(text)

    if _CLEAR_INCLUDED_CARRIERS.search(folded):
        delta["carriers"] = []
    elif included:
        delta["carriers"] = included
        delta["carrier_mode"] = (
            "add"
            if _ADD_CARRIER_CONTEXT.search(folded)
            else "replace"
        )

    if _CLEAR_EXCLUDED_CARRIERS.search(folded):
        delta["excluded_carriers"] = []
    elif excluded:
        delta["excluded_carriers_add"] = excluded

    for fare_preference, pattern in _FARE_PATTERNS:
        if pattern.search(folded):
            delta["fare_preference"] = fare_preference
            break

    if not delta:
        raise ValueError(
            "Todavía no pude identificar un cambio concreto en ese mensaje. "
            "Podés cambiar pasajeros, cabina, fechas, escalas, aerolíneas "
            "o preferencia tarifaria."
        )

    return delta


def _set_date_in_request_data(
    data: dict[str, Any],
    field: str,
    value: date,
) -> None:
    iso_value = value.isoformat()

    if field == "departure_date":
        data["departure_date"] = iso_value
        if data.get("legs"):
            legs = [dict(item) for item in data["legs"]]
            legs[0]["departure_date"] = iso_value
            data["legs"] = legs
        return

    if field == "return_date":
        data["return_date"] = iso_value
        if data.get("legs"):
            legs = [dict(item) for item in data["legs"]]
            if len(legs) != 2:
                raise ValueError(
                    "No puedo identificar con seguridad el tramo de regreso."
                )
            legs[1]["departure_date"] = iso_value
            data["legs"] = legs
        return

    raise ValueError(f"Campo de fecha no soportado: {field}")


def _format_carriers(values: list[str]) -> str:
    return ", ".join(values) if values else "Cualquiera"


def _apply_delta(
    base: QuoteSearchAPIRequest,
    delta: dict[str, Any],
) -> tuple[QuoteSearchAPIRequest, list[QuoteChangeItem]]:
    data = base.model_dump(mode="json")
    changes: list[QuoteChangeItem] = []

    if "adults" in delta:
        before = _passenger_label(base)
        _replace_adults(data, int(delta["adults"]))
        preview = QuoteSearchAPIRequest.model_validate(data)
        after = _passenger_label(preview)
        if before != after:
            changes.append(
                QuoteChangeItem(
                    field="passengers",
                    label="Pasajeros",
                    before=before,
                    after=after,
                )
            )
        data = preview.model_dump(mode="json")

    if "cabin" in delta:
        cabin = Cabin(delta["cabin"])
        before = base.cabin.value
        data["cabin"] = cabin.value
        data["cabins"] = []
        data["outbound_cabin"] = None
        data["return_cabin"] = None
        if before != cabin.value:
            changes.append(
                QuoteChangeItem(
                    field="cabin",
                    label="Cabina",
                    before=before,
                    after=cabin.value,
                )
            )

    if "max_stops" in delta:
        before_stops = 0 if base.direct else base.max_stops
        after_stops = int(delta["max_stops"])
        data["direct"] = bool(
            delta.get("direct", after_stops == 0)
        )
        data["max_stops"] = after_stops
        if before_stops != after_stops:
            changes.append(
                QuoteChangeItem(
                    field="max_stops",
                    label="Escalas máximas",
                    before=before_stops,
                    after=after_stops,
                )
            )

    if "departure_date" in delta:
        before = _base_departure_date(base)
        after = delta["departure_date"]
        assert isinstance(after, date)
        _set_date_in_request_data(
            data,
            "departure_date",
            after,
        )
        if before != after:
            changes.append(
                QuoteChangeItem(
                    field="departure_date",
                    label="Fecha de salida",
                    before=before.isoformat(),
                    after=after.isoformat(),
                )
            )

    if "return_date" in delta:
        before = _base_return_date(base)
        after = delta["return_date"]
        assert isinstance(after, date)
        _set_date_in_request_data(
            data,
            "return_date",
            after,
        )
        if before != after:
            changes.append(
                QuoteChangeItem(
                    field="return_date",
                    label="Fecha de regreso",
                    before=(
                        before.isoformat()
                        if before is not None
                        else None
                    ),
                    after=after.isoformat(),
                )
            )

    if "carriers" in delta:
        before = list(base.carriers)
        requested = list(delta["carriers"])
        if delta.get("carrier_mode") == "add":
            after = sorted(set(before) | set(requested))
        else:
            after = sorted(set(requested))
        data["carriers"] = after

        if before != after:
            changes.append(
                QuoteChangeItem(
                    field="carriers",
                    label="Aerolíneas incluidas",
                    before=_format_carriers(before),
                    after=_format_carriers(after),
                )
            )

    if "excluded_carriers" in delta:
        before = list(base.excluded_carriers)
        after = sorted(set(delta["excluded_carriers"]))
        data["excluded_carriers"] = after

        if before != after:
            changes.append(
                QuoteChangeItem(
                    field="excluded_carriers",
                    label="Aerolíneas excluidas",
                    before=_format_carriers(before),
                    after=_format_carriers(after),
                )
            )

    if "excluded_carriers_add" in delta:
        before = list(base.excluded_carriers)
        after = sorted(
            set(before)
            | set(delta["excluded_carriers_add"])
        )
        data["excluded_carriers"] = after

        current_included = set(data.get("carriers") or [])
        if current_included:
            data["carriers"] = sorted(
                current_included - set(after)
            )

        if before != after:
            changes.append(
                QuoteChangeItem(
                    field="excluded_carriers",
                    label="Aerolíneas excluidas",
                    before=_format_carriers(before),
                    after=_format_carriers(after),
                )
            )

    if "fare_preference" in delta:
        preference = FarePreference(
            delta["fare_preference"]
        )
        before = base.fare_preference
        data["fare_preference"] = preference.value
        if before != preference:
            changes.append(
                QuoteChangeItem(
                    field="fare_preference",
                    label="Tarifa",
                    before=before.value,
                    after=preference.value,
                )
            )

    modified = QuoteSearchAPIRequest.model_validate(data)

    if not changes:
        raise ValueError(
            "El mensaje no cambia la cotización actual."
        )

    return modified, changes


async def modify_stored_quote(
    repo: QuoteRepository,
    quote_id: str,
    request: QuoteModificationRequest,
) -> QuoteModificationResponse:
    record = repo.assert_latest(quote_id)
    base_request = QuoteSearchAPIRequest.model_validate(
        record.search_request
    )

    delta = _interpret_delta(
        request.text,
        base_request,
    )
    modified_request, changes = _apply_delta(
        base_request,
        delta,
    )

    if not request.execute:
        return QuoteModificationResponse(
            base_quote_id=quote_id,
            new_quote_id=None,
            parser="conversation-delta-v1",
            changes=changes,
            search_request=modified_request,
            quote=None,
        )

    modified_request.persist = False
    fresh = await search_quote(modified_request)

    interpretation = {
        "parser": "conversation-delta-v1",
        "base_quote_id": quote_id,
        "changes": [
            item.model_dump(mode="json")
            for item in changes
        ],
    }

    new_id = repo.create(
        request=modified_request,
        response=fresh,
        source="agent_modify",
        agent_text=request.text,
        interpretation=interpretation,
        parent_quote_id=quote_id,
    )
    fresh.quote_id = new_id

    repo.update_workflow(
        new_id,
        client_name=record.client_name,
        client_reference=record.client_reference,
        notes=record.notes,
    )
    repo.link_refresh(quote_id, new_id)

    return QuoteModificationResponse(
        base_quote_id=quote_id,
        new_quote_id=new_id,
        parser="conversation-delta-v1",
        changes=changes,
        search_request=modified_request,
        quote=fresh,
    )
