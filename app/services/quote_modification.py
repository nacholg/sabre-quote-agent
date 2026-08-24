from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.models.api import (
    QuoteChangeItem,
    QuoteModificationRequest,
    QuoteModificationResponse,
    QuoteSearchAPIRequest,
)
from app.models.quote_request import Cabin, PassengerKind, PassengerSpec
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


def _interpret_delta(text: str) -> dict[str, Any]:
    folded = _fold(text)
    delta: dict[str, Any] = {}

    for pattern in _ADULT_COUNT_PATTERNS:
        match = pattern.search(folded)
        if match:
            delta["adults"] = int(match.group("count"))
            break

    for cabin, pattern in _CABIN_PATTERNS:
        if pattern.search(folded):
            delta["cabin"] = cabin
            break

    if _DIRECT_PATTERN.search(folded):
        delta["direct"] = True
        delta["max_stops"] = 0
    elif _ONE_STOP_PATTERN.search(folded):
        delta["direct"] = False
        delta["max_stops"] = 1

    if not delta:
        raise ValueError(
            "Todavía no pude identificar un cambio concreto en ese mensaje. "
            "En esta primera versión podés cambiar cantidad de adultos, "
            "cabina o condición de vuelos directos/una escala."
        )

    return delta


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
        data["direct"] = bool(delta.get("direct", after_stops == 0))
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

    delta = _interpret_delta(request.text)
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
