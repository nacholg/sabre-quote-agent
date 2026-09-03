from __future__ import annotations

from time import sleep
from typing import Any, Callable

from app.config import get_settings
from app.models.booking import (
    BookingRecord,
    BookingStatus,
    PnrAttemptStatus,
)
from app.models.pnr_workspace import (
    PnrAssessmentResult,
    PnrWorkspaceResponse,
    PnrWorkspaceSnapshotRecord,
    PnrWorkspaceStatus,
)
from app.sabre.soap_pnr_read import SabreSoapPnrReadService
from app.services.booking_contact_service import BookingContactService
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)
from app.services.pnr_assessment_service import PnrAssessmentService
from app.services.pnr_pre_issue_readiness_service import (
    build_pnr_pre_issue_readiness,
)
from app.services.pnr_ticketing_constraint_service import (
    interpret_pnr_ticketing_constraint,
)
from app.services.pnr_workspace_snapshot_repository import (
    PnrWorkspaceSnapshotRepository,
)


_PROVIDER = "sabre_travel_itinerary_read"
_READ_ERROR_CODE = "PNR_READ_FAILED"
_READ_ERROR_MESSAGE = (
    "La reserva fue creada, pero no se pudo verificar su estado actual "
    "en Sabre. Reintentá la sincronización."
)
_DEFAULT_READ_ATTEMPTS = 4
_DEFAULT_BACKOFF_SECONDS = 0.5


class PnrWorkspaceStateError(RuntimeError):
    """Booking cannot yet be represented as a post-create PNR Workspace."""


class PnrWorkspaceService:
    """Synchronize the actual Sabre PNR and build the agent workspace.

    A successful remote read replaces the one cached normalized snapshot.
    A failed remote read never changes Create PNR state. If a prior valid
    snapshot exists it is returned as stale, with workspace status READ_ERROR.
    """

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        attempt_service: BookingPnrAttemptService | None = None,
        passenger_service: BookingPassengerService | None = None,
        contact_service: BookingContactService | None = None,
        snapshot_repository: PnrWorkspaceSnapshotRepository | None = None,
        assessment_service: PnrAssessmentService | None = None,
        settings_loader: Callable[[str], Any] | None = None,
        reader_factory: Callable[[Any], Any] | None = None,
        read_attempts: int = _DEFAULT_READ_ATTEMPTS,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if read_attempts < 1:
            raise ValueError("read_attempts debe ser >= 1.")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds debe ser >= 0.")

        self.booking_repository = (
            booking_repository or get_booking_repository()
        )
        self.attempt_service = (
            attempt_service
            or BookingPnrAttemptService(
                booking_repository=self.booking_repository
            )
        )
        self.passenger_service = (
            passenger_service
            or BookingPassengerService(
                booking_repository=self.booking_repository
            )
        )
        self.contact_service = (
            contact_service
            or BookingContactService(
                booking_repository=self.booking_repository
            )
        )
        self.snapshot_repository = (
            snapshot_repository
            or PnrWorkspaceSnapshotRepository(
                booking_repository=self.booking_repository
            )
        )
        self.assessment_service = (
            assessment_service or PnrAssessmentService()
        )
        self.settings_loader = settings_loader or get_settings
        self.reader_factory = (
            reader_factory
            or (lambda settings: SabreSoapPnrReadService(settings))
        )
        self.read_attempts = read_attempts
        self.backoff_seconds = backoff_seconds
        self.sleeper = sleeper or sleep

    def _booking_and_locator(
        self,
        booking_id: str,
    ) -> tuple[BookingRecord, str]:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)

        if booking.status != BookingStatus.PNR_CREATED:
            raise PnrWorkspaceStateError(
                "PNR Workspace requiere un Booking PNR_CREATED."
            )

        attempt = self.attempt_service.get(booking_id)
        if (
            attempt is None
            or attempt.status != PnrAttemptStatus.SUCCEEDED
            or not attempt.confirmation_id
        ):
            raise PnrWorkspaceStateError(
                "PNR Workspace requiere un intento Create PNR SUCCEEDED "
                "con localizador persistido."
            )

        return booking, attempt.confirmation_id

    def _assessment(
        self,
        booking: BookingRecord,
        record: PnrWorkspaceSnapshotRecord,
    ) -> PnrAssessmentResult:
        passengers = self.passenger_service.get(booking.booking_id)
        contact = self.contact_service.get(booking.booking_id)
        return self.assessment_service.assess(
            booking=booking,
            passengers=passengers,
            contact=contact,
            snapshot=record.snapshot,
        )

    def _response_from_record(
        self,
        *,
        booking: BookingRecord,
        record: PnrWorkspaceSnapshotRecord,
        stale: bool,
        status_override: PnrWorkspaceStatus | None = None,
        read_error_code: str | None = None,
        read_error_message: str | None = None,
    ) -> PnrWorkspaceResponse:
        assessment = self._assessment(booking, record)
        final_status = status_override or assessment.assessment.status
        pre_issue_readiness = build_pnr_pre_issue_readiness(
            confirmation_id=record.confirmation_id,
            retrieved_at=record.retrieved_at,
            stale=stale,
            workspace_status=final_status,
            read_error_code=read_error_code,
            assessment=assessment.assessment,
            ticket_candidate=assessment.ticket_candidate,
        )
        ticketing_constraint = interpret_pnr_ticketing_constraint(
            record.snapshot.ticketing
        )
        return PnrWorkspaceResponse(
            booking_id=booking.booking_id,
            confirmation_id=record.confirmation_id,
            provider=record.provider,
            environment=booking.environment,
            status=final_status,
            retrieved_at=record.retrieved_at,
            stale=stale,
            snapshot=record.snapshot,
            assessment=assessment.assessment,
            next_action=assessment.next_action,
            pricing_selection=assessment.pricing_selection,
            pricing_coverage=assessment.pricing_coverage,
            ticket_candidate=assessment.ticket_candidate,
            pre_issue_readiness=pre_issue_readiness,
            ticketing_constraint=ticketing_constraint,
            read_error_code=read_error_code,
            read_error_message=read_error_message,
        )

    def _read_error(
        self,
        *,
        booking: BookingRecord,
        confirmation_id: str,
        code: str = _READ_ERROR_CODE,
    ) -> PnrWorkspaceResponse:
        cached = self.snapshot_repository.latest(
            booking.booking_id
        )
        if (
            cached is not None
            and cached.confirmation_id == confirmation_id
        ):
            return self._response_from_record(
                booking=booking,
                record=cached,
                stale=True,
                status_override=PnrWorkspaceStatus.READ_ERROR,
                read_error_code=code,
                read_error_message=_READ_ERROR_MESSAGE,
            )

        pre_issue_readiness = build_pnr_pre_issue_readiness(
            confirmation_id=confirmation_id,
            retrieved_at=None,
            stale=False,
            workspace_status=PnrWorkspaceStatus.READ_ERROR,
            read_error_code=code,
            assessment=None,
            ticket_candidate=None,
        )
        return PnrWorkspaceResponse(
            booking_id=booking.booking_id,
            confirmation_id=confirmation_id,
            provider=_PROVIDER,
            environment=booking.environment,
            status=PnrWorkspaceStatus.READ_ERROR,
            retrieved_at=None,
            stale=False,
            snapshot=None,
            assessment=None,
            next_action=None,
            pre_issue_readiness=pre_issue_readiness,
            read_error_code=code,
            read_error_message=_READ_ERROR_MESSAGE,
        )

    @staticmethod
    def _expected_segment_count(
        booking: BookingRecord,
    ) -> int | None:
        revision = booking.accepted_offer_revision
        if revision is None:
            return None
        return len(revision.snapshot.segments)

    def _read_with_retry(
        self,
        *,
        booking: BookingRecord,
        reader: Any,
        confirmation_id: str,
    ) -> Any:
        expected_segments = self._expected_segment_count(booking)
        last_error: Exception | None = None
        last_valid_result: Any | None = None
        last_attempt_was_valid = False

        for attempt_index in range(self.read_attempts):
            try:
                result = reader.retrieve(confirmation_id)

                if (
                    result.confirmation_id != confirmation_id
                    or result.snapshot.confirmation_id != confirmation_id
                ):
                    return result

                actual_segments = len(result.snapshot.segments)
            except Exception as exc:
                last_error = exc
                last_attempt_was_valid = False
            else:
                last_valid_result = result
                last_error = None
                last_attempt_was_valid = True

                if (
                    expected_segments is None
                    or actual_segments == expected_segments
                ):
                    return result

            if attempt_index + 1 < self.read_attempts:
                delay = self.backoff_seconds * (2 ** attempt_index)
                self.sleeper(delay)

        if last_valid_result is not None and last_attempt_was_valid:
            return last_valid_result

        if last_error is not None:
            raise last_error

        raise RuntimeError("Sabre PNR read agotó los intentos sin resultado.")

    def get(
        self,
        booking_id: str,
    ) -> PnrWorkspaceResponse:
        booking, confirmation_id = self._booking_and_locator(
            booking_id
        )

        try:
            settings = self.settings_loader(booking.environment)
            reader = self.reader_factory(settings)
            result = self._read_with_retry(
                booking=booking,
                reader=reader,
                confirmation_id=confirmation_id,
            )
        except Exception:
            # A post-create read failure must never be reclassified as a
            # Create PNR failure. The UI can safely retry this GET.
            return self._read_error(
                booking=booking,
                confirmation_id=confirmation_id,
            )

        if (
            result.confirmation_id != confirmation_id
            or result.snapshot.confirmation_id != confirmation_id
        ):
            return self._read_error(
                booking=booking,
                confirmation_id=confirmation_id,
                code="PNR_LOCATOR_MISMATCH",
            )

        record = self.snapshot_repository.save(
            booking_id=booking.booking_id,
            confirmation_id=confirmation_id,
            provider=_PROVIDER,
            environment=booking.environment,
            snapshot=result.snapshot,
        )
        return self._response_from_record(
            booking=booking,
            record=record,
            stale=False,
        )


def get_pnr_workspace_service() -> PnrWorkspaceService:
    return PnrWorkspaceService()
