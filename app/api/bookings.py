from fastapi import APIRouter, HTTPException, status

from app.models.booking import (
    BookingContactRecord,
    BookingContactUpdateRequest,
    BookingCreateRequest,
    BookingPassengersResponse,
    BookingPassengersUpdateRequest,
    BookingRecord,
    BookingReviewResponse,
)
from app.services.booking_contact_service import (
    BookingContactLockedError,
    BookingContactRevisionConflictError,
    BookingContactValidationError,
    get_booking_contact_service,
)
from app.services.booking_passenger_service import (
    BookingPassengerLockedError,
    BookingPassengerValidationError,
    BookingRevisionConflictError,
    get_booking_passenger_service,
)
from app.services.booking_review_service import get_booking_review_service
from app.services.booking_repository import (
    BookingIdempotencyConflictError,
    get_booking_repository,
)
from app.services.booking_service import (
    BookingSelectionError,
    get_booking_service,
)
from app.services.quote_repository import QuoteVersionConflictError


router = APIRouter(tags=["bookings"])


@router.post(
    "/quotes/{quote_id}/bookings",
    response_model=BookingRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Iniciar Booking desde una selección exacta",
)
async def create_booking(
    quote_id: str,
    request: BookingCreateRequest,
) -> BookingRecord:
    try:
        return get_booking_service().create_from_quote(
            quote_id,
            request,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Cotización no encontrada: {quote_id}",
        )
    except QuoteVersionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except BookingIdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except BookingSelectionError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@router.get(
    "/bookings/{booking_id}",
    response_model=BookingRecord,
    summary="Leer Booking",
)
async def get_booking(
    booking_id: str,
) -> BookingRecord:
    booking = get_booking_repository().get(booking_id)
    if booking is None:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )
    return booking


@router.get(
    "/bookings/{booking_id}/passengers",
    response_model=BookingPassengersResponse,
    summary="Leer pasajeros del Booking",
)
async def get_booking_passengers(
    booking_id: str,
) -> BookingPassengersResponse:
    try:
        return get_booking_passenger_service().get(booking_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )


@router.put(
    "/bookings/{booking_id}/passengers",
    response_model=BookingPassengersResponse,
    summary="Guardar identidades de pasajeros",
)
async def update_booking_passengers(
    booking_id: str,
    request: BookingPassengersUpdateRequest,
) -> BookingPassengersResponse:
    try:
        return get_booking_passenger_service().update(
            booking_id,
            request,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )
    except BookingRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except BookingPassengerLockedError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except BookingPassengerValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@router.get(
    "/bookings/{booking_id}/contact",
    response_model=BookingContactRecord,
    summary="Leer contacto del Booking",
)
async def get_booking_contact(
    booking_id: str,
) -> BookingContactRecord:
    try:
        return get_booking_contact_service().get(booking_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )


@router.put(
    "/bookings/{booking_id}/contact",
    response_model=BookingContactRecord,
    summary="Guardar contacto del Booking",
)
async def update_booking_contact(
    booking_id: str,
    request: BookingContactUpdateRequest,
) -> BookingContactRecord:
    try:
        return get_booking_contact_service().update(
            booking_id,
            request,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )
    except BookingContactRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except BookingContactLockedError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except BookingContactValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@router.get(
    "/bookings/{booking_id}/review",
    response_model=BookingReviewResponse,
    summary="Leer review canónico del Booking",
)
async def get_booking_review(
    booking_id: str,
) -> BookingReviewResponse:
    try:
        return get_booking_review_service().get(booking_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )
