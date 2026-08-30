from __future__ import annotations

from typing import Any

from sqlalchemy import update

from app.db.models import BookingRow
from app.models.booking import (
    BookingCreatePnrRequest,
    BookingPnrAttemptRecord,
    BookingStatus,
    PnrAttemptStatus,
)
from app.sabre.create_booking import (
    SabreCreateBookingAmbiguousFailure,
    SabreCreateBookingDisabledError,
    SabreCreateBookingSafeFailure,
)
from app.services.booking_create_pnr_builder import BookingCreatePnrPayloadBuilder
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import BookingRepository, get_booking_repository
from app.services.booking_state import require_transition


BOOKING_TABLE = BookingRow.__table__


class BookingPnrExecutionReconciliationRequiredError(RuntimeError):
    pass


class BookingPnrExecutionBindingError(RuntimeError):
    pass


class BookingPnrExecutionLocalConsistencyError(RuntimeError):
    pass


class BookingPnrExecutionService:
    """Own the persisted non-idempotent Create Booking execution."""

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        attempt_service: BookingPnrAttemptService | None = None,
        payload_builder: BookingCreatePnrPayloadBuilder | None = None,
        provider: Any,
    ) -> None:
        self.booking_repository = booking_repository or get_booking_repository()
        self.attempt_service = attempt_service or BookingPnrAttemptService(
            booking_repository=self.booking_repository
        )
        self.payload_builder = payload_builder or BookingCreatePnrPayloadBuilder(
            booking_repository=self.booking_repository
        )
        self.provider = provider

    def _assert_binding_current(self, attempt: BookingPnrAttemptRecord) -> None:
        booking = self.booking_repository.get(attempt.booking_id)
        if booking is None:
            raise KeyError(attempt.booking_id)
        if booking.revision != attempt.booking_revision:
            raise BookingPnrExecutionBindingError(
                "El Booking cambió después de preparar Create PNR."
            )
        if booking.accepted_offer_revision_id != attempt.accepted_offer_revision_id:
            raise BookingPnrExecutionBindingError(
                "La oferta aceptada no coincide con el intento PNR."
            )
        if booking.status != BookingStatus.READY_TO_CREATE_PNR:
            raise BookingPnrExecutionBindingError(
                "El Booking ya no está READY_TO_CREATE_PNR."
            )

    def _finalize_booking_success(self, attempt: BookingPnrAttemptRecord) -> None:
        booking = self.booking_repository.get(attempt.booking_id)
        if booking is None:
            raise KeyError(attempt.booking_id)
        if booking.status == BookingStatus.PNR_CREATED:
            return

        require_transition(booking.status, BookingStatus.PNR_CREATED)
        if booking.revision != attempt.booking_revision:
            raise BookingPnrExecutionLocalConsistencyError(
                "Sabre creó el PNR pero el Booking local cambió."
            )

        with self.booking_repository.engine.begin() as connection:
            result = connection.execute(
                update(BOOKING_TABLE)
                .where(
                    BOOKING_TABLE.c.booking_id == attempt.booking_id,
                    BOOKING_TABLE.c.revision == attempt.booking_revision,
                    BOOKING_TABLE.c.status
                    == BookingStatus.READY_TO_CREATE_PNR.value,
                )
                .values(
                    status=BookingStatus.PNR_CREATED.value,
                    revision=attempt.booking_revision + 1,
                )
            )
            if result.rowcount != 1:
                raise BookingPnrExecutionLocalConsistencyError(
                    "Sabre creó el PNR pero no se pudo finalizar el Booking local."
                )

    async def execute(
        self,
        booking_id: str,
        request: BookingCreatePnrRequest,
    ) -> BookingPnrAttemptRecord:
        attempt = self.attempt_service.prepare(booking_id, request)

        if attempt.status == PnrAttemptStatus.SUCCEEDED:
            self._finalize_booking_success(attempt)
            return self.attempt_service.get(booking_id) or attempt

        if attempt.status in {
            PnrAttemptStatus.SUBMITTING,
            PnrAttemptStatus.RECONCILIATION_REQUIRED,
        }:
            raise BookingPnrExecutionReconciliationRequiredError(
                "El intento PNR no puede reenviarse automáticamente."
            )

        self._assert_binding_current(attempt)
        payload, fingerprint = self.payload_builder.build_with_fingerprint(booking_id)

        if (
            attempt.request_fingerprint is not None
            and attempt.request_fingerprint != fingerprint
        ):
            raise BookingPnrExecutionBindingError(
                "El payload no coincide con el fingerprint persistido."
            )

        attempt = self.attempt_service.mark_submitting(
            attempt.pnr_attempt_id,
            request_fingerprint=fingerprint,
        )

        try:
            result = await self.provider.create_booking(
                payload,
                environment=attempt.environment,
            )
        except (SabreCreateBookingSafeFailure, SabreCreateBookingDisabledError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            return self.attempt_service.mark_failed_safe(
                attempt.pnr_attempt_id,
                error_code=str(code),
                error_message=str(exc)[:1000],
            )
        except SabreCreateBookingAmbiguousFailure as exc:
            self.attempt_service.mark_reconciliation_required(
                attempt.pnr_attempt_id,
                error_code=exc.code,
                error_message=str(exc)[:1000],
            )
            raise BookingPnrExecutionReconciliationRequiredError(str(exc)) from exc
        except Exception as exc:
            self.attempt_service.mark_reconciliation_required(
                attempt.pnr_attempt_id,
                error_code="UNEXPECTED_PROVIDER_ERROR",
                error_message=type(exc).__name__,
            )
            raise BookingPnrExecutionReconciliationRequiredError(
                "Error inesperado después de SUBMITTING; requiere reconciliación."
            ) from exc

        succeeded = self.attempt_service.mark_succeeded(
            attempt.pnr_attempt_id,
            confirmation_id=result.confirmation_id,
            provider_reference=result.provider_reference,
        )
        self._finalize_booking_success(succeeded)
        return succeeded
