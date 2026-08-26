from __future__ import annotations

from app.models.api import StoredQuoteRecord
from app.models.booking import (
    BookingCreateRequest,
    BookingOfferSnapshot,
    BookingRecord,
)
from app.models.itinerary import ItineraryOption
from app.models.quote_request import PassengerKind, PassengerSpec
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)
from app.services.quote_repository import (
    QuoteRepository,
    get_quote_repository,
)


class BookingSelectionError(ValueError):
    """Raised when Reserve cannot resolve one exact server-side product."""


def _selected_itinerary(
    record: StoredQuoteRecord,
    rank: int,
) -> ItineraryOption:
    raw_options = list(
        record.quote_response.get("options") or []
    ) + list(
        record.quote_response.get("_candidate_options") or []
    )

    for item in raw_options:
        if int(item.get("rank") or 0) == rank:
            return ItineraryOption.model_validate(item["itinerary"])

    raise BookingSelectionError(
        f"No se encontró el itinerario exacto para rank {rank}."
    )


def _passenger_mix(
    record: StoredQuoteRecord,
) -> list[PassengerSpec]:
    raw_specs = record.search_request.get("passengers") or []
    if raw_specs:
        return [
            PassengerSpec.model_validate(spec)
            for spec in raw_specs
        ]

    # Backward compatibility for historical quote payloads.
    result = [
        PassengerSpec(
            type=PassengerKind.ADULT,
            quantity=int(record.search_request.get("adults") or 1),
        )
    ]

    children = int(record.search_request.get("children") or 0)
    if children:
        result.append(
            PassengerSpec(
                type=PassengerKind.CHILD,
                quantity=children,
                age=int(record.search_request.get("child_age") or 6),
            )
        )

    infants = int(record.search_request.get("infants") or 0)
    if infants:
        result.append(
            PassengerSpec(
                type=PassengerKind.INFANT,
                quantity=infants,
            )
        )

    return result


def _environment(record: StoredQuoteRecord) -> str:
    value = str(
        record.search_request.get("environment")
        or record.quote_response.get("environment")
        or "cert"
    ).lower()

    if value not in {"cert", "prod"}:
        raise BookingSelectionError(
            f"Entorno inválido para Booking: {value}."
        )
    return value


class BookingService:
    def __init__(
        self,
        *,
        quote_repository: QuoteRepository | None = None,
        booking_repository: BookingRepository | None = None,
    ) -> None:
        self.quote_repository = (
            quote_repository or get_quote_repository()
        )
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )

    def create_from_quote(
        self,
        quote_id: str,
        request: BookingCreateRequest,
    ) -> BookingRecord:
        # Historical quote versions are never valid Reserve sources.
        record = self.quote_repository.assert_latest(quote_id)

        if request.rank not in record.selected_ranks:
            raise BookingSelectionError(
                "La opción elegida para Reservar no forma parte de la "
                "selección persistida de la cotización."
            )

        selected_fare = next(
            (
                item
                for item in record.selected_fares
                if int(item.rank) == request.rank
            ),
            None,
        )
        if selected_fare is None:
            raise BookingSelectionError(
                "La opción elegida no tiene una tarifa exacta persistida. "
                "Volvé a seleccionar la tarifa antes de Reservar."
            )

        itinerary = _selected_itinerary(record, request.rank)
        missing_booking_class = [
            index + 1
            for index, segment in enumerate(itinerary.segments)
            if not segment.booking_class
        ]
        if missing_booking_class:
            raise BookingSelectionError(
                "No se puede congelar el producto sin booking class en "
                "todos los segmentos. Segmentos: "
                + ", ".join(str(index) for index in missing_booking_class)
                + "."
            )

        snapshot = BookingOfferSnapshot(
            source_quote_id=quote_id,
            rank=request.rank,
            fare_index=selected_fare.fare_index,
            segments=itinerary.segments,
            fare=selected_fare.fare,
            passenger_mix=_passenger_mix(record),
        )

        return self.booking_repository.create_initial(
            source_quote_id=quote_id,
            selected_rank=request.rank,
            environment=_environment(record),
            client_request_id=str(request.client_request_id),
            snapshot=snapshot,
        )


def get_booking_service() -> BookingService:
    return BookingService()
