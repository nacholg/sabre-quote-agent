from __future__ import annotations

from app.models.booking import (
    BookingCreatePnrRequest,
    BookingPnrAttemptRecord,
    BookingRevalidationRequest,
    BookingStatus,
    RevalidationStatus,
)
from app.services.booking_pnr_attempt_service import (
    BookingPnrAttemptIdempotencyConflictError,
    BookingPnrAttemptRevisionConflictError,
    BookingPnrAttemptService,
)
from app.services.booking_pnr_execution_service import (
    BookingPnrExecutionService,
)
from app.services.booking_revalidation_service import (
    BookingRevalidationService,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


class BookingCreatePnrFreshRevalidationError(RuntimeError):
    """Fresh pre-Create-PNR revalidation did not preserve the exact product."""


class BookingCreatePnrWorkflowService:
    """Atomic application workflow: fresh revalidation then Create Booking.

    The browser's revision is the revision visible when Create PNR is clicked.
    Revalidation may advance Booking by one revision. Create Booking must bind
    itself to that newly accepted MATCHED revision, never to the stale one.

    Existing PNR attempts are never revalidated/recreated. They are resumed
    through the persisted idempotency/reconciliation state.
    """

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        revalidation_service: BookingRevalidationService | None = None,
        execution_service: BookingPnrExecutionService,
        attempt_service: BookingPnrAttemptService | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )
        self.revalidation_service = (
            revalidation_service
            or BookingRevalidationService(
                booking_repository=self.booking_repository
            )
        )
        self.execution_service = execution_service
        self.attempt_service = (
            attempt_service
            or BookingPnrAttemptService(
                booking_repository=self.booking_repository
            )
        )

    async def execute(
        self,
        booking_id: str,
        request: BookingCreatePnrRequest,
    ) -> BookingPnrAttemptRecord:
        # If an attempt already exists, never perform a new availability
        # check followed by another Create Booking. Resume only that exact
        # persisted idempotent attempt.
        existing = self.attempt_service.get(booking_id)
        if existing is not None:
            if existing.client_request_id != str(request.client_request_id):
                raise BookingPnrAttemptIdempotencyConflictError(
                    "El Booking ya tiene un intento de Create PNR ligado "
                    "a otro client_request_id."
                )

            replay_request = BookingCreatePnrRequest(
                revision=existing.booking_revision,
                client_request_id=request.client_request_id,
            )
            return await self.execution_service.execute(
                booking_id,
                replay_request,
            )

        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)

        if request.revision != booking.revision:
            raise BookingPnrAttemptRevisionConflictError(
                "El Booking cambio desde que abriste Create PNR. "
                f"Actual={booking.revision}, recibida={request.revision}."
            )

        # Mandatory fresh pre-write revalidation.
        revalidation = await self.revalidation_service.revalidate(
            booking_id,
            BookingRevalidationRequest(
                revision=request.revision,
            ),
        )

        if (
            revalidation.revalidation_status
            != RevalidationStatus.MATCHED
            or revalidation.status
            != BookingStatus.READY_TO_CREATE_PNR
        ):
            raise BookingCreatePnrFreshRevalidationError(
                "La oferta seleccionada ya no coincide exactamente con "
                "la disponibilidad/tarifa actual. Create Booking no fue "
                "enviado."
            )

        # Revalidation accepted a new immutable candidate revision.
        # Bind the non-idempotent write to that fresh revision.
        fresh_request = BookingCreatePnrRequest(
            revision=revalidation.booking_revision,
            client_request_id=request.client_request_id,
        )

        return await self.execution_service.execute(
            booking_id,
            fresh_request,
        )
