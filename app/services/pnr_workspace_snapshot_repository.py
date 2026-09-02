from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import BookingPnrSnapshotRow
from app.models.pnr_workspace import (
    PnrSnapshot,
    PnrWorkspaceSnapshotRecord,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


PNR_SNAPSHOT_TABLE = BookingPnrSnapshotRow.__table__


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PnrWorkspaceSnapshotRepository:
    """Persist only the latest normalized PNR snapshot for each Booking."""

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )

    @staticmethod
    def _record(row) -> PnrWorkspaceSnapshotRecord:
        return PnrWorkspaceSnapshotRecord(
            booking_id=row["booking_id"],
            confirmation_id=row["confirmation_id"],
            provider=row["provider"],
            environment=str(row["environment"]).lower(),
            retrieved_at=row["retrieved_at"],
            snapshot=PnrSnapshot.model_validate(
                json.loads(row["snapshot_json"])
            ),
        )

    def latest(
        self,
        booking_id: str,
    ) -> PnrWorkspaceSnapshotRecord | None:
        with self.booking_repository.engine.connect() as connection:
            row = (
                connection.execute(
                    select(PNR_SNAPSHOT_TABLE).where(
                        PNR_SNAPSHOT_TABLE.c.booking_id == booking_id
                    )
                )
                .mappings()
                .first()
            )
        return self._record(row) if row is not None else None

    def save(
        self,
        *,
        booking_id: str,
        confirmation_id: str,
        provider: str,
        environment: str,
        snapshot: PnrSnapshot,
    ) -> PnrWorkspaceSnapshotRecord:
        if snapshot.confirmation_id != confirmation_id:
            raise ValueError(
                "El snapshot normalizado no coincide con el localizador "
                "que se intenta persistir."
            )

        values = {
            "booking_id": booking_id,
            "confirmation_id": confirmation_id,
            "provider": provider,
            "environment": environment.lower(),
            "retrieved_at": _utc_now(),
            "snapshot_json": json.dumps(
                snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        update_values = {
            key: value
            for key, value in values.items()
            if key != "booking_id"
        }

        dialect = self.booking_repository.dialect_name
        with self.booking_repository.engine.begin() as connection:
            if dialect == "sqlite":
                statement = sqlite_insert(PNR_SNAPSHOT_TABLE).values(
                    **values
                )
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            PNR_SNAPSHOT_TABLE.c.booking_id
                        ],
                        set_=update_values,
                    )
                )
            elif dialect == "postgresql":
                statement = postgresql_insert(
                    PNR_SNAPSHOT_TABLE
                ).values(**values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            PNR_SNAPSHOT_TABLE.c.booking_id
                        ],
                        set_=update_values,
                    )
                )
            else:
                existing = connection.execute(
                    select(PNR_SNAPSHOT_TABLE.c.booking_id).where(
                        PNR_SNAPSHOT_TABLE.c.booking_id == booking_id
                    )
                ).first()
                if existing is None:
                    connection.execute(
                        insert(PNR_SNAPSHOT_TABLE).values(**values)
                    )
                else:
                    connection.execute(
                        update(PNR_SNAPSHOT_TABLE)
                        .where(
                            PNR_SNAPSHOT_TABLE.c.booking_id
                            == booking_id
                        )
                        .values(**update_values)
                    )

        persisted = self.latest(booking_id)
        if persisted is None:
            raise RuntimeError(
                "No se pudo releer el snapshot PNR persistido."
            )
        return persisted
