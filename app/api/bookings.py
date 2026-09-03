from fastapi import APIRouter, HTTPException, status

from app.models.booking import (
    BookingContactRecord,
    BookingContactUpdateRequest,
    BookingCreatePnrRequest,
    BookingCreateRequest,
    BookingPassengersResponse,
    BookingPnrAttemptRecord,
    BookingPassengersUpdateRequest,
    BookingRecord,
    BookingRevalidationRequest,
    BookingRevalidationResponse,
    BookingReviewResponse,
)
from app.models.pnr_workspace import PnrWorkspaceResponse
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
from app.services.booking_create_pnr_builder import (
    BookingCreatePnrPayloadError,
)
from app.services.booking_create_pnr_service import (
    BookingCreatePnrUnavailableError,
    get_booking_create_pnr_service,
)
from app.services.booking_create_pnr_workflow_service import (
    BookingCreatePnrFreshRevalidationError,
)
from app.services.booking_pnr_attempt_service import (
    BookingPnrAttemptIdempotencyConflictError,
    BookingPnrAttemptRevisionConflictError,
    BookingPnrAttemptStateError,
    get_booking_pnr_attempt_service,
)
from app.services.booking_pnr_execution_service import (
    BookingPnrExecutionBindingError,
    BookingPnrExecutionLocalConsistencyError,
    BookingPnrExecutionReconciliationRequiredError,
)
from app.services.pnr_workspace_service import (
    PnrWorkspaceStateError,
    get_pnr_workspace_service,
)
from app.services.booking_review_service import get_booking_review_service
from app.services.booking_revalidation_service import (
    BookingRevalidationConflictError,
    BookingRevalidationDataError,
    BookingRevalidationStateError,
    get_booking_revalidation_service,
)
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


@router.get(
    "/bookings/{booking_id}/pnr",
    response_model=BookingPnrAttemptRecord,
    summary="Leer intento Create PNR",
)
async def get_booking_pnr_attempt(
    booking_id: str,
) -> BookingPnrAttemptRecord:
    booking = get_booking_repository().get(booking_id)
    if booking is None:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )

    attempt = get_booking_pnr_attempt_service().get(booking_id)
    if attempt is None:
        raise HTTPException(
            status_code=404,
            detail="El Booking todavía no tiene un intento Create PNR.",
        )
    return attempt


@router.post(
    "/bookings/{booking_id}/pnr",
    response_model=BookingPnrAttemptRecord,
    summary="Revalidar y crear PNR exacto",
)
async def create_booking_pnr(
    booking_id: str,
    request: BookingCreatePnrRequest,
) -> BookingPnrAttemptRecord:
    try:
        return await get_booking_create_pnr_service().execute(
            booking_id,
            request,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )
    except BookingCreatePnrUnavailableError as exc:
        # Important: this happens before any persisted attempt/write.
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    except (
        BookingCreatePnrFreshRevalidationError,
        BookingPnrAttemptIdempotencyConflictError,
        BookingPnrAttemptRevisionConflictError,
        BookingPnrAttemptStateError,
        BookingPnrExecutionBindingError,
        BookingPnrExecutionLocalConsistencyError,
        BookingPnrExecutionReconciliationRequiredError,
        BookingRevalidationConflictError,
        BookingRevalidationStateError,
    ) as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except (
        BookingCreatePnrPayloadError,
        BookingRevalidationDataError,
    ) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@router.get(
    "/bookings/{booking_id}/pnr-workspace",
    response_model=PnrWorkspaceResponse,
    summary="Sincronizar y leer PNR Workspace",
)
async def get_booking_pnr_workspace(
    booking_id: str,
) -> PnrWorkspaceResponse:
    try:
        return get_pnr_workspace_service().get(booking_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )
    except PnrWorkspaceStateError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@router.get(
    "/bookings/{booking_id}/revalidation",
    response_model=BookingRevalidationResponse,
    summary="Leer última revalidación del Booking",
)
async def get_booking_revalidation(
    booking_id: str,
) -> BookingRevalidationResponse:
    try:
        return get_booking_revalidation_service().get(booking_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )


@router.post(
    "/bookings/{booking_id}/revalidation",
    response_model=BookingRevalidationResponse,
    summary="Revalidar producto exacto con Sabre",
)
async def revalidate_booking(
    booking_id: str,
    request: BookingRevalidationRequest,
) -> BookingRevalidationResponse:
    try:
        return await get_booking_revalidation_service().revalidate(
            booking_id,
            request,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Reserva no encontrada: {booking_id}",
        )
    except (
        BookingRevalidationConflictError,
        BookingRevalidationStateError,
    ) as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except BookingRevalidationDataError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
