from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, insert, inspect, select, update
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

from app.db.database import database_url as configured_database_url
from app.db.models import BookingOfferRevisionRow, BookingRow
from app.models.booking import (
    BookingOfferRevision,
    BookingOfferSnapshot,
    BookingOfferSource,
    BookingRecord,
    BookingStatus,
    RevalidationStatus,
)


BOOKING_TABLE = BookingRow.__table__
OFFER_REVISION_TABLE = BookingOfferRevisionRow.__table__


class BookingIdempotencyConflictError(RuntimeError):
    """Raised when one client request id is reused for a different Booking."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _booking_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"B-{stamp}-{uuid.uuid4().hex[:8].upper()}"


def _sqlite_url(db_path: str | Path) -> str:
    path = Path(db_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _engine_for_url(url: str) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if make_url(url).get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


class BookingRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.database_url = (
            _sqlite_url(db_path)
            if db_path is not None
            else configured_database_url()
        )
        self.engine = _engine_for_url(self.database_url)
        self._init_db()

    @property
    def dialect_name(self) -> str:
        return self.engine.dialect.name

    def close(self) -> None:
        self.engine.dispose()

    def _init_db(self) -> None:
        if self.dialect_name == "sqlite":
            BookingRow.metadata.create_all(self.engine)
            return

        inspector = inspect(self.engine)
        missing = [
            table_name
            for table_name in (
                "bookings",
                "booking_offer_revisions",
                "booking_passengers",
                "booking_contacts",
                "booking_revalidations",
                "booking_pnr_attempts",
                "booking_pnr_snapshots",
            )
            if not inspector.has_table(table_name)
        ]
        if missing:
            raise RuntimeError(
                "La base configurada no está migrada para Booking. "
                "Ejecutá 'alembic upgrade head' antes de usar el funnel. "
                f"Faltan tablas: {', '.join(missing)}"
            )

    def _offer_revision(
        self,
        offer_revision_id: int | None,
    ) -> BookingOfferRevision | None:
        if offer_revision_id is None:
            return None

        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(OFFER_REVISION_TABLE).where(
                        OFFER_REVISION_TABLE.c.offer_revision_id
                        == offer_revision_id
                    )
                )
                .mappings()
                .first()
            )

        if row is None:
            return None

        return BookingOfferRevision(
            offer_revision_id=int(row["offer_revision_id"]),
            booking_id=row["booking_id"],
            revision_number=int(row["revision_number"]),
            source=row["source"],
            snapshot=json.loads(row["snapshot_json"]),
            created_at=row["created_at"],
            accepted_at=row["accepted_at"],
        )

    def _to_record(self, row) -> BookingRecord:
        accepted_offer_revision_id = row["accepted_offer_revision_id"]
        return BookingRecord(
            booking_id=row["booking_id"],
            source_quote_id=row["source_quote_id"],
            selected_rank=int(row["selected_rank"]),
            environment=str(row["environment"]).lower(),
            status=row["status"],
            revalidation_status=row["revalidation_status"],
            accepted_offer_revision_id=(
                int(accepted_offer_revision_id)
                if accepted_offer_revision_id is not None
                else None
            ),
            revision=int(row["revision"]),
            client_request_id=row["client_request_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            abandoned_at=row["abandoned_at"],
            accepted_offer_revision=self._offer_revision(
                int(accepted_offer_revision_id)
                if accepted_offer_revision_id is not None
                else None
            ),
        )

    def get(self, booking_id: str) -> BookingRecord | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(BOOKING_TABLE).where(
                        BOOKING_TABLE.c.booking_id == booking_id
                    )
                )
                .mappings()
                .first()
            )

        return self._to_record(row) if row is not None else None

    def get_by_client_request_id(
        self,
        client_request_id: str,
    ) -> BookingRecord | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(BOOKING_TABLE).where(
                        BOOKING_TABLE.c.client_request_id
                        == client_request_id
                    )
                )
                .mappings()
                .first()
            )

        return self._to_record(row) if row is not None else None

    @staticmethod
    def _validate_idempotent_match(
        existing: BookingRecord,
        *,
        source_quote_id: str,
        selected_rank: int,
    ) -> BookingRecord:
        if (
            existing.source_quote_id != source_quote_id
            or existing.selected_rank != selected_rank
        ):
            raise BookingIdempotencyConflictError(
                "client_request_id ya fue utilizado para otra reserva."
            )
        return existing

    def create_initial(
        self,
        *,
        source_quote_id: str,
        selected_rank: int,
        environment: str,
        client_request_id: str,
        snapshot: BookingOfferSnapshot,
    ) -> BookingRecord:
        existing = self.get_by_client_request_id(client_request_id)
        if existing is not None:
            return self._validate_idempotent_match(
                existing,
                source_quote_id=source_quote_id,
                selected_rank=selected_rank,
            )

        booking_id = _booking_id()
        now = _utc_now()

        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(BOOKING_TABLE).values(
                        booking_id=booking_id,
                        source_quote_id=source_quote_id,
                        selected_rank=selected_rank,
                        environment=environment.lower(),
                        status=BookingStatus.DRAFT.value,
                        revalidation_status=RevalidationStatus.NOT_RUN.value,
                        accepted_offer_revision_id=None,
                        revision=1,
                        client_request_id=client_request_id,
                        created_at=now,
                        updated_at=now,
                        abandoned_at=None,
                    )
                )

                result = connection.execute(
                    insert(OFFER_REVISION_TABLE).values(
                        booking_id=booking_id,
                        revision_number=1,
                        source=BookingOfferSource.INITIAL.value,
                        snapshot_json=json.dumps(
                            snapshot.model_dump(mode="json"),
                            ensure_ascii=False,
                        ),
                        created_at=now,
                        accepted_at=now,
                    )
                )
                offer_revision_id = int(result.inserted_primary_key[0])

                connection.execute(
                    update(BOOKING_TABLE)
                    .where(BOOKING_TABLE.c.booking_id == booking_id)
                    .values(
                        accepted_offer_revision_id=offer_revision_id,
                        updated_at=now,
                    )
                )
        except IntegrityError:
            # Network retries / double-clicks can race. The unique request id
            # is the final authority; resolve the winning row and return it.
            existing = self.get_by_client_request_id(client_request_id)
            if existing is None:
                raise
            return self._validate_idempotent_match(
                existing,
                source_quote_id=source_quote_id,
                selected_rank=selected_rank,
            )

        created = self.get(booking_id)
        if created is None:
            raise RuntimeError(
                f"No se pudo releer la reserva creada: {booking_id}"
            )
        return created


_repository: BookingRepository | None = None


def get_booking_repository() -> BookingRepository:
    global _repository

    desired_url = configured_database_url()
    if (
        _repository is None
        or _repository.database_url != desired_url
    ):
        if _repository is not None:
            _repository.close()
        _repository = BookingRepository()

    return _repository


def reset_booking_repository_for_tests() -> None:
    global _repository

    if _repository is not None:
        _repository.close()
    _repository = None
