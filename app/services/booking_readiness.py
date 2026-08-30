from __future__ import annotations

from collections import Counter

from app.models.api import (
    BookingDraftRecord,
    BookingReadinessIssue,
    BookingReadinessResponse,
    QuoteFareSelection,
    StoredQuoteRecord,
)
from app.models.itinerary import ItineraryOption


def _expected_passengers(
    record: StoredQuoteRecord,
) -> dict[str, int]:
    request = record.search_request
    result = {"ADT": 0, "CHD": 0, "INF": 0}

    specs = request.get("passengers") or []
    if specs:
        for spec in specs:
            raw_type = str(spec.get("type") or "").upper()
            quantity = int(spec.get("quantity") or 0)
            booking_type = {
                "ADT": "ADT",
                "CHILD": "CHD",
                "CHD": "CHD",
                "INF": "INF",
            }.get(raw_type)
            if booking_type:
                result[booking_type] += quantity
        return result

    result["ADT"] = int(request.get("adults") or 0)
    result["CHD"] = int(request.get("children") or 0)
    result["INF"] = int(request.get("infants") or 0)
    return result


def _provided_passengers(
    draft: BookingDraftRecord,
) -> dict[str, int]:
    counts = Counter(
        passenger.passenger_type
        for passenger in draft.passengers
    )
    return {
        "ADT": counts.get("ADT", 0),
        "CHD": counts.get("CHD", 0),
        "INF": counts.get("INF", 0),
    }


def _issue(code: str, message: str) -> BookingReadinessIssue:
    return BookingReadinessIssue(
        code=code,
        message=message,
    )


def _selected_itinerary(
    record: StoredQuoteRecord,
    rank: int,
) -> ItineraryOption | None:
    raw_options = list(
        record.quote_response.get("options") or []
    ) + list(
        record.quote_response.get("_candidate_options") or []
    )

    for item in raw_options:
        if int(item.get("rank") or 0) == rank:
            return ItineraryOption.model_validate(
                item["itinerary"]
            )

    return None


def assess_booking_readiness(
    record: StoredQuoteRecord,
    draft: BookingDraftRecord,
) -> BookingReadinessResponse:
    blockers: list[BookingReadinessIssue] = []
    warnings: list[BookingReadinessIssue] = []

    selected_rank: int | None = None
    selected_fare: QuoteFareSelection | None = None

    if record.refreshed_quote_id:
        blockers.append(
            _issue(
                "historical_quote_version",
                "La cotización tiene una versión más nueva. "
                "Usá la última versión antes de reservar.",
            )
        )

    if len(record.selected_ranks) != 1:
        blockers.append(
            _issue(
                "single_option_required",
                "Create PNR requiere exactamente una opción "
                "seleccionada.",
            )
        )
    else:
        selected_rank = int(record.selected_ranks[0])
        selected_fare = next(
            (
                item
                for item in record.selected_fares
                if int(item.rank) == selected_rank
            ),
            None,
        )

        if selected_fare is None:
            blockers.append(
                _issue(
                    "exact_fare_required",
                    "La opción debe tener una branded fare "
                    "exacta guardada antes de reservar.",
                )
            )
        else:
            if not selected_fare.fare.fare_basis_codes:
                warnings.append(
                    _issue(
                        "fare_basis_missing",
                        "Sabre no informó fare basis en la tarifa "
                        "seleccionada; deberá revalidarse antes de reservar.",
                    )
                )

        itinerary = _selected_itinerary(
            record,
            selected_rank,
        )
        if itinerary is None:
            blockers.append(
                _issue(
                    "selected_itinerary_missing",
                    "No se encontró el itinerario seleccionado "
                    "dentro de la cotización guardada.",
                )
            )
        else:
            missing_booking_class = [
                index + 1
                for index, segment in enumerate(itinerary.segments)
                if not segment.booking_class
            ]
            if missing_booking_class:
                blockers.append(
                    _issue(
                        "booking_class_missing",
                        "Falta booking class en los segmentos: "
                        + ", ".join(
                            str(index)
                            for index in missing_booking_class
                        )
                        + ".",
                    )
                )

    expected = _expected_passengers(record)
    provided = _provided_passengers(draft)

    if expected != provided:
        blockers.append(
            _issue(
                "passenger_mix_mismatch",
                "La cantidad/tipo de pasajeros del borrador "
                "no coincide con la búsqueda original.",
            )
        )

    for index, passenger in enumerate(
        draft.passengers,
        start=1,
    ):
        if not passenger.given_name or not passenger.surname:
            blockers.append(
                _issue(
                    "passenger_name_missing",
                    f"Pasajero {index}: falta nombre o apellido.",
                )
            )

        if (
            passenger.passenger_type in {"CHD", "INF"}
            and passenger.date_of_birth is None
        ):
            blockers.append(
                _issue(
                    "passenger_dob_missing",
                    f"Pasajero {index}: la fecha de nacimiento "
                    "es obligatoria para CHD/INF.",
                )
            )

    adult_count = provided.get("ADT", 0)
    if adult_count > 1:
        for index, passenger in enumerate(
            draft.passengers,
            start=1,
        ):
            if (
                passenger.passenger_type == "INF"
                and passenger.associated_adult_index is None
            ):
                blockers.append(
                    _issue(
                        "infant_adult_association_missing",
                        f"Pasajero {index}: indicá a qué adulto "
                        "está asociado el INF.",
                    )
                )

    if not draft.contact.email and not draft.contact.phone:
        blockers.append(
            _issue(
                "contact_missing",
                "Informá al menos un email o teléfono de contacto.",
            )
        )

    if not draft.received_from:
        blockers.append(
            _issue(
                "received_from_missing",
                "Falta Received From para cerrar la futura "
                "transacción de PNR.",
            )
        )

    warnings.append(
        _issue(
            "live_revalidation_required",
            "Antes de Create PNR habrá que validar nuevamente "
            "disponibilidad y precio en Sabre.",
        )
    )
    warnings.append(
        _issue(
            "documents_not_validated",
            "v0.31 valida datos mínimos para crear la reserva; "
            "documentos, APIS y Secure Flight se validarán "
            "en una etapa posterior.",
        )
    )

    return BookingReadinessResponse(
        quote_id=record.quote_id,
        ready=not blockers,
        selected_rank=selected_rank,
        selected_fare=selected_fare,
        expected_passengers=expected,
        provided_passengers=provided,
        draft=draft,
        blockers=blockers,
        warnings=warnings,
        requires_live_revalidation=True,
    )
