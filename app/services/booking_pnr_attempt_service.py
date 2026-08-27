from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import BookingPnrAttemptRow
from app.models.booking import (
    BookingCreatePnrRequest,
    BookingPnrAttemptRecord,
    PnrAttemptStatus,
)
from app.services.booking_create_pnr_readiness_service import (
    BookingCreatePnrReadinessService,
)
from app.services.booking_pnr_state import require_pnr_attempt_transition
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


PNR_ATTEMPT_TABLE = BookingPnrAttemptRow.__table__
_PROVIDER = "sabre_booking_management"


class BookingPnrAttemptIdempotencyConflictError(RuntimeError):
    """An idempotency key or Booking is already bound to another attempt."""


class BookingPnrAttemptRevisionConflictError(RuntimeError):
    """Booking changed after the Create PNR screen was loaded."""


class BookingPnrAttemptStateError(RuntimeError):
    """Booking does not satisfy the server-side Create PNR readiness gate."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class BookingPnrAttemptService:
    """Persist the one-and-only Create PNR attempt before any Sabre write.

    Part 1 deliberately stops at PREPARED. No network/provider call exists in
    this service yet.
    """

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        readiness_service: BookingCreatePnrReadinessService | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )
        self.readiness_service = (
            readiness_service
            or BookingCreatePnrReadinessService(
                booking_repository=self.booking_repository
            )
        )

    @staticmethod
    def _record(row) -> BookingPnrAttemptRecord:
        return BookingPnrAttemptRecord(
            pnr_attempt_id=int(row["pnr_attempt_id"]),
            booking_id=row["booking_id"],
            client_request_id=row["client_request_id"],
            booking_revision=int(row["booking_revision"]),
            accepted_offer_revision_id=int(
                row["accepted_offer_revision_id"]
            ),
            revalidation_id=int(row["revalidation_id"]),
            environment=str(row["environment"]).lower(),
            provider=row["provider"],
            status=row["status"],
            confirmation_id=row["confirmation_id"],
            provider_reference=row["provider_reference"],
            request_fingerprint=row["request_fingerprint"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            submitted_at=row["submitted_at"],
            completed_at=row["completed_at"],
        )

    def _row_by_client_request_id(
        self,
        client_request_id: str,
    ):
        with self.booking_repository.engine.connect() as connection:
            return (
                connection.execute(
                    select(PNR_ATTEMPT_TABLE).where(
                        PNR_ATTEMPT_TABLE.c.client_request_id
                        == client_request_id
                    )
                )
                .mappings()
                .first()
            )

    def _row_by_booking_id(self, booking_id: str):
        with self.booking_repository.engine.connect() as connection:
            return (
                connection.execute(
                    select(PNR_ATTEMPT_TABLE).where(
                        PNR_ATTEMPT_TABLE.c.booking_id == booking_id
                    )
                )
                .mappings()
                .first()
            )

    @staticmethod
    def _validate_existing(
        row,
        *,
        booking_id: str,
        request: BookingCreatePnrRequest,
    ) -> BookingPnrAttemptRecord:
        request_id = str(request.client_request_id)
        if (
            row["booking_id"] != booking_id
            or row["client_request_id"] != request_id
            or int(row["booking_revision"]) != request.revision
        ):
            raise BookingPnrAttemptIdempotencyConflictError(
                "client_request_id ya está ligado a otro intento de PNR "
                "o a otra revisión del Booking."
            )
        return BookingPnrAttemptService._record(row)

    def get(self, booking_id: str) -> BookingPnrAttemptRecord | None:
        row = self._row_by_booking_id(booking_id)
        return self._record(row) if row is not None else None

    def prepare(
        self,
        booking_id: str,
        request: BookingCreatePnrRequest,
    ) -> BookingPnrAttemptRecord:
        request_id = str(request.client_request_id)

        # Exact network retry: return the original attempt without re-evaluating
        # mutable Booking state.
        existing_by_key = self._row_by_client_request_id(request_id)
        if existing_by_key is not None:
            return self._validate_existing(
                existing_by_key,
                booking_id=booking_id,
                request=request,
            )

        # Stronger than client idempotency: one Booking can never get a second
        # PNR creation attempt under a different key.
        existing_by_booking = self._row_by_booking_id(booking_id)
        if existing_by_booking is not None:
            raise BookingPnrAttemptIdempotencyConflictError(
                "El Booking ya tiene un intento de creación de PNR. "
                "Debe continuarse o reconciliarse ese mismo intento."
            )

        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)

        if request.revision != booking.revision:
            raise BookingPnrAttemptRevisionConflictError(
                "El Booking cambió desde que abriste Create PNR. "
                f"Recargá antes de continuar (actual {booking.revision}, "
                f"recibida {request.revision})."
            )

        readiness = self.readiness_service.get(booking_id)
        if not readiness.ready:
            details = ", ".join(readiness.reasons) or "not_ready"
            raise BookingPnrAttemptStateError(
                "El Booking no pasó el Create PNR readiness gate: "
                f"{details}."
            )

        if (
            readiness.accepted_offer_revision_id is None
            or readiness.revalidation_id is None
        ):
            raise BookingPnrAttemptStateError(
                "El Booking no tiene una oferta/revalidación MATCHED "
                "persistida para ligar el intento de PNR."
            )

        now = _utc_now()

        try:
            with self.booking_repository.engine.begin() as connection:
                result = connection.execute(
                    insert(PNR_ATTEMPT_TABLE).values(
                        booking_id=booking_id,
                        client_request_id=request_id,
                        booking_revision=booking.revision,
                        accepted_offer_revision_id=(
                            readiness.accepted_offer_revision_id
                        ),
                        revalidation_id=readiness.revalidation_id,
                        environment=booking.environment,
                        provider=_PROVIDER,
                        status=PnrAttemptStatus.PREPARED.value,
                        confirmation_id=None,
                        provider_reference=None,
                        request_fingerprint=None,
                        error_code=None,
                        error_message=None,
                        created_at=now,
                        updated_at=now,
                        submitted_at=None,
                        completed_at=None,
                    )
                )
                attempt_id = int(result.inserted_primary_key[0])
        except IntegrityError:
            # Cross-thread/process race: unique constraints are authoritative.
            winner = self._row_by_client_request_id(request_id)
            if winner is not None:
                return self._validate_existing(
                    winner,
                    booking_id=booking_id,
                    request=request,
                )

            winner = self._row_by_booking_id(booking_id)
            if winner is not None:
                raise BookingPnrAttemptIdempotencyConflictError(
                    "El Booking ya tiene otro intento de creación de PNR."
                )
            raise

        with self.booking_repository.engine.connect() as connection:
            row = (
                connection.execute(
                    select(PNR_ATTEMPT_TABLE).where(
                        PNR_ATTEMPT_TABLE.c.pnr_attempt_id == attempt_id
                    )
                )
                .mappings()
                .first()
            )

        if row is None:
            raise RuntimeError(
                "No se pudo releer el intento de creación de PNR."
            )
        return self._record(row)

    def _row_by_attempt_id(self, pnr_attempt_id: int):
        with self.booking_repository.engine.connect() as connection:
            return (
                connection.execute(
                    select(PNR_ATTEMPT_TABLE).where(
                        PNR_ATTEMPT_TABLE.c.pnr_attempt_id == pnr_attempt_id
                    )
                )
                .mappings()
                .first()
            )

    def get_by_attempt_id(
        self,
        pnr_attempt_id: int,
    ) -> BookingPnrAttemptRecord | None:
        row = self._row_by_attempt_id(pnr_attempt_id)
        return self._record(row) if row is not None else None

    def _transition(
        self,
        pnr_attempt_id: int,
        target: PnrAttemptStatus,
        *,
        request_fingerprint: str | None = None,
        confirmation_id: str | None = None,
        provider_reference: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> BookingPnrAttemptRecord:
        row = self._row_by_attempt_id(pnr_attempt_id)
        if row is None:
            raise KeyError(pnr_attempt_id)

        current = PnrAttemptStatus(row["status"])
        require_pnr_attempt_transition(current, target)

        existing_fingerprint = row["request_fingerprint"]
        if (
            request_fingerprint is not None
            and existing_fingerprint is not None
            and existing_fingerprint != request_fingerprint
        ):
            raise BookingPnrAttemptStateError(
                "El payload Create Booking cambió respecto del fingerprint persistido."
            )

        now = _utc_now()
        values: dict[str, object] = {
            "status": target.value,
            "updated_at": now,
        }
        if request_fingerprint is not None:
            values["request_fingerprint"] = request_fingerprint

        if target == PnrAttemptStatus.SUBMITTING:
            if request_fingerprint is None and existing_fingerprint is None:
                raise BookingPnrAttemptStateError(
                    "SUBMITTING requiere request_fingerprint persistido."
                )
            values.update(
                submitted_at=now,
                completed_at=None,
                confirmation_id=None,
                provider_reference=None,
                error_code=None,
                error_message=None,
            )
        elif target == PnrAttemptStatus.SUCCEEDED:
            if not confirmation_id:
                raise BookingPnrAttemptStateError(
                    "SUCCEEDED requiere confirmation_id."
                )
            values.update(
                confirmation_id=confirmation_id,
                provider_reference=provider_reference,
                error_code=None,
                error_message=None,
                completed_at=now,
            )
        elif target in {
            PnrAttemptStatus.FAILED_SAFE,
            PnrAttemptStatus.RECONCILIATION_REQUIRED,
        }:
            values.update(
                error_code=error_code,
                error_message=error_message,
                completed_at=now,
            )

        with self.booking_repository.engine.begin() as connection:
            result = connection.execute(
                update(PNR_ATTEMPT_TABLE)
                .where(
                    PNR_ATTEMPT_TABLE.c.pnr_attempt_id == pnr_attempt_id,
                    PNR_ATTEMPT_TABLE.c.status == current.value,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise BookingPnrAttemptStateError(
                    "El intento PNR cambió en paralelo; recargá antes de continuar."
                )

        updated = self.get_by_attempt_id(pnr_attempt_id)
        if updated is None:
            raise RuntimeError("No se pudo releer el intento PNR.")
        return updated

    def mark_submitting(
        self,
        pnr_attempt_id: int,
        *,
        request_fingerprint: str,
    ) -> BookingPnrAttemptRecord:
        return self._transition(
            pnr_attempt_id,
            PnrAttemptStatus.SUBMITTING,
            request_fingerprint=request_fingerprint,
        )

    def mark_succeeded(
        self,
        pnr_attempt_id: int,
        *,
        confirmation_id: str,
        provider_reference: str | None = None,
    ) -> BookingPnrAttemptRecord:
        return self._transition(
            pnr_attempt_id,
            PnrAttemptStatus.SUCCEEDED,
            confirmation_id=confirmation_id,
            provider_reference=provider_reference,
        )

    def mark_failed_safe(
        self,
        pnr_attempt_id: int,
        *,
        error_code: str,
        error_message: str,
    ) -> BookingPnrAttemptRecord:
        return self._transition(
            pnr_attempt_id,
            PnrAttemptStatus.FAILED_SAFE,
            error_code=error_code,
            error_message=error_message,
        )

    def mark_reconciliation_required(
        self,
        pnr_attempt_id: int,
        *,
        error_code: str,
        error_message: str,
    ) -> BookingPnrAttemptRecord:
        return self._transition(
            pnr_attempt_id,
            PnrAttemptStatus.RECONCILIATION_REQUIRED,
            error_code=error_code,
            error_message=error_message,
        )


def get_booking_pnr_attempt_service() -> BookingPnrAttemptService:
    return BookingPnrAttemptService()
