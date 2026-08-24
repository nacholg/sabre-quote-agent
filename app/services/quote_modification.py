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
from app.services.agent_parser import (
    _carrier_sets,
    _parse_compact_dates_in_text,
    _parse_dates,
)
from app.services.llm_prompt_normalizer import (
    LLMInterpreterUnavailable,
    llm_fallback_enabled,
)
from app.services.llm_quote_modification_normalizer import (
    normalize_quote_modification_with_llm,
)
from app.services.quote_repository import QuoteRepository
from app.services.quote_service import search_quote


class QuoteModificationClarificationRequired(ValueError):
    """The follow-up lacks factual detail that must not be guessed."""


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

_UNSUPPORTED_ROUTE_CHANGE_PATTERN = re.compile(
    r"\b(?:cambiar|cambia|cambiame|cambiemos|modificar|modifica|mover)\b"
    r"[^.;]{0,40}\b(?:origen|destino|ruta)\b"
    r"|"
    r"\b(?:salir|partir|saliendo|partiendo)\s+desde\b"
    r"|"
    r"\b(?:agregar|agrega|sumar|suma|anadir|anade)\b"
    r"[^.;]{0,32}\b(?:tramo|destino|origen)\b"
)

_UNSUPPORTED_MINOR_CHANGE_PATTERN = re.compile(
    r"\b(?:nino|ninos|nina|ninas|menor|menores|child|children|"
    r"infante|infantes|infant|infants|bebe|bebes|c\d{1,2}|inf)\b"
)

_RELATIVE_PASSENGER_CHANGE_PATTERN = re.compile(
    r"\b(?:agregar|agrega|sumar|suma|quitar|quita|sacar|saca)\b"
    r"[^.;]{0,32}\b(?:persona|personas|pasajero|pasajeros|"
    r"adulto|adultos|adt)\b"
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


def _guard_unsupported_change(text: str) -> None:
    folded = _fold(text)

    if _UNSUPPORTED_ROUTE_CHANGE_PATTERN.search(folded):
        raise QuoteModificationClarificationRequired(
            "Cambiar origen, destino, ruta o agregar tramos todavía no está "
            "soportado en una modificación conversacional. Creá una nueva "
            "cotización para cambiar origen, destino o tramos."
        )

    if _UNSUPPORTED_MINOR_CHANGE_PATTERN.search(folded):
        raise QuoteModificationClarificationRequired(
            "Modificar menores, infantes o sus edades todavía no está "
            "soportado en una cotización existente. Creá una nueva "
            "cotización indicando la composición completa de pasajeros."
        )

    if _RELATIVE_PASSENGER_CHANGE_PATTERN.search(folded):
        raise QuoteModificationClarificationRequired(
            "Para cambiar pasajeros indicá el nuevo total de adultos "
            "(por ejemplo, 'cotizar para 3 personas') en lugar de agregar "
            "o quitar pasajeros relativamente."
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
        raise QuoteModificationClarificationRequired(
            "El mensaje mezcla salida y regreso con una sola fecha. "
            "Indicá cada cambio por separado o escribí ambas fechas."
        )

    if has_return_context:
        if len(base.legs) > 2:
            raise QuoteModificationClarificationRequired(
                "Para un itinerario multitramos indicá qué tramo querés "
                "modificar; no voy a asumir cuál es la vuelta."
            )
        if _base_return_date(base) is None:
            raise QuoteModificationClarificationRequired(
                "La cotización actual no tiene regreso. "
                "Todavía no agrego un tramo nuevo de vuelta "
                "desde una modificación conversacional."
            )
        return "return_date"

    if has_departure_context:
        return "departure_date"

    if _base_return_date(base) is not None:
        raise QuoteModificationClarificationRequired(
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

    today = date.today()
    parsed_departure, parsed_return, _ = _parse_dates(
        normalized,
        today,
    )

    # The hybrid normalizer uses compact Sabre-style dates such as
    # 03NOV2026. The main agent parser already understands these tokens,
    # so the modification parser should accept the same canonical syntax.
    if parsed_departure is None:
        compact_dates = _parse_compact_dates_in_text(
            normalized,
            today,
        )
        if compact_dates:
            parsed_departure = compact_dates[0]
            if len(compact_dates) >= 2:
                parsed_return = compact_dates[1]

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
    _guard_unsupported_change(text)
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
        if re.search(
            r"\b(?:algo|opcion|alternativa)?\s*"
            r"(?:mejor|mejora|conveniente)\b",
            folded,
        ):
            raise QuoteModificationClarificationRequired(
                "Todavía no pude identificar un cambio concreto en ese mensaje. "
                "Decime qué querés priorizar: precio, duración, menos escalas "
                "u otra condición."
            )
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

    parser = "conversation-delta-v1"
    assumptions: list[str] = []
    warnings: list[str] = []

    try:
        delta = _interpret_delta(
            request.text,
            base_request,
        )
    except QuoteModificationClarificationRequired:
        raise
    except ValueError as deterministic_error:
        if not llm_fallback_enabled(base_request.environment):
            raise

        print(
            "[MOD] llm fallback start "
            f"env={base_request.environment.upper()}"
        )
        try:
            normalized = await normalize_quote_modification_with_llm(
                request.text,
                base=base_request,
                today=date.today(),
                environment=base_request.environment,
            )
        except LLMInterpreterUnavailable:
            print("[MOD] llm fallback unavailable")
            raise deterministic_error

        if normalized.needs_clarification:
            print("[MOD] llm clarification required")
            raise QuoteModificationClarificationRequired(
                normalized.clarification
                or "Necesito una aclaración antes de modificar la cotización."
            )

        try:
            delta = _interpret_delta(
                normalized.canonical_instruction,
                base_request,
            )
        except QuoteModificationClarificationRequired:
            raise
        except ValueError as normalized_error:
            raise deterministic_error from normalized_error

        parser = "conversation-hybrid-llm-v1"
        assumptions = list(normalized.assumptions)
        warnings = list(normalized.warnings)
        print(
            "[MOD] llm fallback complete "
            f"parser={parser}"
        )

    modified_request, changes = _apply_delta(
        base_request,
        delta,
    )

    if not request.execute:
        return QuoteModificationResponse(
            base_quote_id=quote_id,
            new_quote_id=None,
            parser=parser,
            assumptions=assumptions,
            warnings=warnings,
            changes=changes,
            search_request=modified_request,
            quote=None,
        )

    modified_request.persist = False
    fresh = await search_quote(modified_request)

    interpretation = {
        "parser": parser,
        "base_quote_id": quote_id,
        "assumptions": assumptions,
        "warnings": warnings,
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
        parser=parser,
        assumptions=assumptions,
        warnings=warnings,
        changes=changes,
        search_request=modified_request,
        quote=fresh,
    )
