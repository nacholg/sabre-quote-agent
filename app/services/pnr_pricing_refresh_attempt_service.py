from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import BookingPnrPricingRefreshAttemptRow
from app.models.pnr_workspace import (
    PnrPricingRefreshAttemptRecord,
    PnrPricingRefreshAttemptStatus,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


ATTEMPT_TABLE = BookingPnrPricingRefreshAttemptRow.__table__


class PnrPricingRefreshAttemptConflictError(RuntimeError):
    """Another active pricing refresh already owns this Booking."""

    def __init__(
        self,
        message: str,
        *,
        attempt: PnrPricingRefreshAttemptRecord | None = None,
    ) -> None:
        super().__init__(message)
        self.attempt = attempt


class PnrPricingRefreshAttemptIdempotencyError(RuntimeError):
    """A client request id was reused for a different pricing identity."""


class PnrPricingRefreshAttemptStateError(RuntimeError):
    """Persisted pricing-refresh lifecycle changed unexpectedly."""


@dataclass(frozen=True)
class PnrPricingRefreshAttemptPrepareResult:
    attempt: PnrPricingRefreshAttemptRecord
    created: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_code(value: str, *, field: str) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _normalize_total(value: Decimal) -> Decimal:
    total = Decimal(value)
    if total < 0:
        raise ValueError("expected_total must be >= 0")
    return total


class PnrPricingRefreshAttemptService:
    """DB-backed single-flight/idempotency guard for pricing mutations.

    `active_booking_id` is populated only while an attempt is PREPARED,
    SUBMITTING, or RECONCILIATION_REQUIRED. Its unique constraint is the
    cross-process mutex: two workers cannot own one Booking at the same time.

    RECONCILIATION_REQUIRED deliberately keeps the active key. It must never
    auto-expire because Sabre may already have committed the pricing write.
    """

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )

    @staticmethod
    def _record(row) -> PnrPricingRefreshAttemptRecord:
        return PnrPricingRefreshAttemptRecord(
            pricing_refresh_attempt_id=int(
                row["pricing_refresh_attempt_id"]
            ),
            booking_id=row["booking_id"],
            client_request_id=row["client_request_id"],
            confirmation_id=row["confirmation_id"],
            expected_brand_code=row["expected_brand_code"],
            expected_currency=row["expected_currency"],
            expected_total=Decimal(row["expected_total"]),
            status=row["status"],
            pricing_authority_id=row["pricing_authority_id"],
            result_json=row["result_json"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            submitted_at=row["submitted_at"],
            completed_at=row["completed_at"],
        )

    def _row_by_client_request_id(self, client_request_id: str):
        with self.booking_repository.engine.connect() as connection:
            return (
                connection.execute(
                    select(ATTEMPT_TABLE).where(
                        ATTEMPT_TABLE.c.client_request_id
                        == client_request_id
                    )
                )
                .mappings()
                .first()
            )

    def _row_by_attempt_id(self, attempt_id: int):
        with self.booking_repository.engine.connect() as connection:
            return (
                connection.execute(
                    select(ATTEMPT_TABLE).where(
                        ATTEMPT_TABLE.c.pricing_refresh_attempt_id
                        == attempt_id
                    )
                )
                .mappings()
                .first()
            )

    def _row_active_by_booking_id(self, booking_id: str):
        with self.booking_repository.engine.connect() as connection:
            return (
                connection.execute(
                    select(ATTEMPT_TABLE).where(
                        ATTEMPT_TABLE.c.active_booking_id == booking_id
                    )
                )
                .mappings()
                .first()
            )

    def get(
        self,
        attempt_id: int,
    ) -> PnrPricingRefreshAttemptRecord | None:
        row = self._row_by_attempt_id(attempt_id)
        return self._record(row) if row is not None else None

    def active_for_booking(
        self,
        booking_id: str,
    ) -> PnrPricingRefreshAttemptRecord | None:
        row = self._row_active_by_booking_id(booking_id)
        return self._record(row) if row is not None else None

    @staticmethod
    def _validate_existing(
        row,
        *,
        booking_id: str,
        client_request_id: str,
        expected_brand_code: str,
        expected_currency: str,
        expected_total: Decimal,
    ) -> PnrPricingRefreshAttemptRecord:
        if (
            row["booking_id"] != booking_id
            or row["client_request_id"] != client_request_id
            or row["expected_brand_code"] != expected_brand_code
            or row["expected_currency"] != expected_currency
            or Decimal(row["expected_total"]) != expected_total
        ):
            raise PnrPricingRefreshAttemptIdempotencyError(
                "client_request_id ya está ligado a otro Booking o a otra "
                "identidad tarifaria."
            )
        return PnrPricingRefreshAttemptService._record(row)

    def prepare(
        self,
        *,
        booking_id: str,
        client_request_id: str,
        expected_brand_code: str,
        expected_currency: str,
        expected_total: Decimal,
        confirmation_id: str | None = None,
    ) -> PnrPricingRefreshAttemptPrepareResult:
        request_id = str(client_request_id or "").strip()
        if not request_id:
            raise ValueError("client_request_id is required")

        brand = _normalize_code(
            expected_brand_code,
            field="expected_brand_code",
        )
        currency = _normalize_code(
            expected_currency,
            field="expected_currency",
        )
        total = _normalize_total(expected_total)

        exact = self._row_by_client_request_id(request_id)
        if exact is not None:
            return PnrPricingRefreshAttemptPrepareResult(
                attempt=self._validate_existing(
                    exact,
                    booking_id=booking_id,
                    client_request_id=request_id,
                    expected_brand_code=brand,
                    expected_currency=currency,
                    expected_total=total,
                ),
                created=False,
            )

        active = self._row_active_by_booking_id(booking_id)
        if active is not None:
            record = self._record(active)
            raise PnrPricingRefreshAttemptConflictError(
                "El Booking ya tiene un refresh de pricing activo. "
                "No se inicia un segundo write.",
                attempt=record,
            )

        now = _utc_now()
        try:
            with self.booking_repository.engine.begin() as connection:
                result = connection.execute(
                    insert(ATTEMPT_TABLE).values(
                        booking_id=booking_id,
                        active_booking_id=booking_id,
                        client_request_id=request_id,
                        confirmation_id=(
                            str(confirmation_id).strip().upper()
                            if confirmation_id
                            else None
                        ),
                        expected_brand_code=brand,
                        expected_currency=currency,
                        expected_total=str(total),
                        status=(
                            PnrPricingRefreshAttemptStatus.PREPARED.value
                        ),
                        pricing_authority_id=None,
                        result_json=None,
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
            # Cross-worker race: DB unique constraints decide the winner.
            exact = self._row_by_client_request_id(request_id)
            if exact is not None:
                return PnrPricingRefreshAttemptPrepareResult(
                    attempt=self._validate_existing(
                        exact,
                        booking_id=booking_id,
                        client_request_id=request_id,
                        expected_brand_code=brand,
                        expected_currency=currency,
                        expected_total=total,
                    ),
                    created=False,
                )

            active = self._row_active_by_booking_id(booking_id)
            if active is not None:
                record = self._record(active)
                raise PnrPricingRefreshAttemptConflictError(
                    "Otro proceso adquirió el refresh de pricing para "
                    "este Booking.",
                    attempt=record,
                )
            raise

        attempt = self.get(attempt_id)
        if attempt is None:
            raise RuntimeError(
                "No se pudo releer el intento de refresh de pricing."
            )
        return PnrPricingRefreshAttemptPrepareResult(
            attempt=attempt,
            created=True,
        )

    def _transition(
        self,
        attempt_id: int,
        *,
        allowed_from: set[PnrPricingRefreshAttemptStatus],
        target: PnrPricingRefreshAttemptStatus,
        result_json: str | None = None,
        pricing_authority_id: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> PnrPricingRefreshAttemptRecord:
        row = self._row_by_attempt_id(attempt_id)
        if row is None:
            raise KeyError(attempt_id)

        current = PnrPricingRefreshAttemptStatus(row["status"])
        if current not in allowed_from:
            raise PnrPricingRefreshAttemptStateError(
                f"Transición inválida {current.value} -> {target.value}."
            )

        now = _utc_now()
        values: dict[str, object] = {
            "status": target.value,
            "updated_at": now,
        }

        if target == PnrPricingRefreshAttemptStatus.SUBMITTING:
            values.update(
                submitted_at=now,
                completed_at=None,
                error_code=None,
                error_message=None,
            )
        else:
            values.update(
                completed_at=now,
                result_json=result_json,
                pricing_authority_id=pricing_authority_id,
                error_code=error_code,
                error_message=error_message,
            )

            if target in {
                PnrPricingRefreshAttemptStatus.SUCCEEDED,
                PnrPricingRefreshAttemptStatus.FAILED_SAFE,
                PnrPricingRefreshAttemptStatus.NO_WRITE,
            }:
                values["active_booking_id"] = None
            elif (
                target
                == PnrPricingRefreshAttemptStatus.RECONCILIATION_REQUIRED
            ):
                # Deliberately retain the DB mutex. Never auto-expire this.
                values["active_booking_id"] = row["booking_id"]

        with self.booking_repository.engine.begin() as connection:
            result = connection.execute(
                update(ATTEMPT_TABLE)
                .where(
                    ATTEMPT_TABLE.c.pricing_refresh_attempt_id
                    == attempt_id,
                    ATTEMPT_TABLE.c.status == current.value,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise PnrPricingRefreshAttemptStateError(
                    "El intento de pricing cambió en paralelo; no se "
                    "continúa con el write."
                )

        updated = self.get(attempt_id)
        if updated is None:
            raise RuntimeError(
                "No se pudo releer el intento de pricing actualizado."
            )
        return updated

    def mark_submitting(
        self,
        attempt_id: int,
    ) -> PnrPricingRefreshAttemptRecord:
        return self._transition(
            attempt_id,
            allowed_from={PnrPricingRefreshAttemptStatus.PREPARED},
            target=PnrPricingRefreshAttemptStatus.SUBMITTING,
        )

    def mark_no_write(
        self,
        attempt_id: int,
        *,
        result_json: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> PnrPricingRefreshAttemptRecord:
        return self._transition(
            attempt_id,
            allowed_from={PnrPricingRefreshAttemptStatus.PREPARED},
            target=PnrPricingRefreshAttemptStatus.NO_WRITE,
            result_json=result_json,
            error_code=error_code,
            error_message=error_message,
        )

    def mark_succeeded(
        self,
        attempt_id: int,
        *,
        result_json: str,
        pricing_authority_id: int | None,
    ) -> PnrPricingRefreshAttemptRecord:
        return self._transition(
            attempt_id,
            allowed_from={PnrPricingRefreshAttemptStatus.SUBMITTING},
            target=PnrPricingRefreshAttemptStatus.SUCCEEDED,
            result_json=result_json,
            pricing_authority_id=pricing_authority_id,
        )

    def mark_failed_safe(
        self,
        attempt_id: int,
        *,
        result_json: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> PnrPricingRefreshAttemptRecord:
        return self._transition(
            attempt_id,
            allowed_from={PnrPricingRefreshAttemptStatus.SUBMITTING},
            target=PnrPricingRefreshAttemptStatus.FAILED_SAFE,
            result_json=result_json,
            error_code=error_code,
            error_message=error_message,
        )

    def mark_reconciliation_required(
        self,
        attempt_id: int,
        *,
        result_json: str,
        error_code: str,
        error_message: str,
    ) -> PnrPricingRefreshAttemptRecord:
        return self._transition(
            attempt_id,
            allowed_from={PnrPricingRefreshAttemptStatus.SUBMITTING},
            target=(
                PnrPricingRefreshAttemptStatus.RECONCILIATION_REQUIRED
            ),
            result_json=result_json,
            error_code=error_code,
            error_message=error_message,
        )


def get_pnr_pricing_refresh_attempt_service(
) -> PnrPricingRefreshAttemptService:
    return PnrPricingRefreshAttemptService()
