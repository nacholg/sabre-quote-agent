from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, delete, insert, inspect, select, update
from sqlalchemy.engine import Engine, make_url

from app.db.database import database_url as configured_database_url
from app.db.models import QuoteArtifactRow, QuoteRow
from app.models.api import (
    AgentInterpretation,
    QuoteSearchAPIRequest,
    QuoteSearchAPIResponse,
    StoredQuoteRecord,
    StoredQuoteSummary,
    QuoteSelectionResponse,
    QuoteWorkflowResponse,
)


QUOTE_TABLE = QuoteRow.__table__
ARTIFACT_TABLE = QuoteArtifactRow.__table__


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _quote_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"Q-{stamp}-{uuid.uuid4().hex[:8].upper()}"


def _sqlite_url(db_path: str | Path) -> tuple[str, Path]:
    path = Path(db_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{path.as_posix()}", path


def _engine_for_url(url: str) -> Engine:
    kwargs: dict[str, Any] = {
        "pool_pre_ping": True,
    }

    if make_url(url).get_backend_name() == "sqlite":
        kwargs["connect_args"] = {
            "check_same_thread": False,
        }

    return create_engine(url, **kwargs)


class QuoteRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is not None:
            url, sqlite_path = _sqlite_url(db_path)
            self.database_url = url
            self.db_path: Path | None = sqlite_path
        else:
            self.database_url = configured_database_url()
            parsed = make_url(self.database_url)

            if parsed.get_backend_name() == "sqlite" and parsed.database:
                self.db_path = Path(parsed.database)
            else:
                self.db_path = None

        self.engine = _engine_for_url(self.database_url)
        self._init_db()

    @property
    def dialect_name(self) -> str:
        return self.engine.dialect.name

    def close(self) -> None:
        self.engine.dispose()

    def _connect(self) -> sqlite3.Connection:
        # Legacy test/debug compatibility for SQLite only.
        # Repository operations no longer use sqlite3.
        if self.dialect_name != "sqlite" or self.db_path is None:
            raise RuntimeError(
                "_connect() is legacy SQLite-only compatibility. "
                "Use SQLAlchemy for portable database access."
            )

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        if self.dialect_name == "sqlite":
            # Local/test compatibility: new SQLite DBs bootstrap themselves.
            QuoteRow.metadata.create_all(self.engine)
            return

        # PostgreSQL/production must be migrated explicitly through Alembic.
        inspector = inspect(self.engine)
        missing = [
            table_name
            for table_name in ("quotes", "quote_artifacts")
            if not inspector.has_table(table_name)
        ]
        if missing:
            raise RuntimeError(
                "La base configurada no está migrada. "
                "Ejecutá 'alembic upgrade head' antes de iniciar la app. "
                f"Faltan tablas: {', '.join(missing)}"
            )

    def create(
        self,
        *,
        request: QuoteSearchAPIRequest,
        response: QuoteSearchAPIResponse,
        source: str = "structured",
        agent_text: str | None = None,
        interpretation: AgentInterpretation | dict[str, Any] | None = None,
        parent_quote_id: str | None = None,
    ) -> str:
        quote_id = response.quote_id or _quote_id()
        now = _utc_now()

        response_copy = response.model_copy(
            update={"quote_id": quote_id}
        )

        interpretation_data = (
            interpretation.model_dump(mode="json")
            if isinstance(interpretation, AgentInterpretation)
            else interpretation
        )

        values = {
            "quote_id": quote_id,
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "source": source,
            "agent_text": agent_text,
            "interpretation_json": (
                json.dumps(
                    interpretation_data,
                    ensure_ascii=False,
                )
                if interpretation_data is not None
                else None
            ),
            "search_request_json": request.model_dump_json(),
            "quote_response_json": response_copy.model_dump_json(),
            "selected_ranks_json": "[]",
            "client_name": None,
            "client_reference": None,
            "notes": None,
            "sent_at": None,
            "parent_quote_id": parent_quote_id,
            "refreshed_quote_id": None,
        }

        with self.engine.begin() as connection:
            connection.execute(
                insert(QUOTE_TABLE).values(**values)
            )

        return quote_id

    def attach_agent_context(
        self,
        quote_id: str,
        *,
        text: str,
        interpretation: AgentInterpretation,
    ) -> None:
        now = _utc_now()

        with self.engine.begin() as connection:
            result = connection.execute(
                update(QUOTE_TABLE)
                .where(QUOTE_TABLE.c.quote_id == quote_id)
                .values(
                    source="agent",
                    agent_text=text,
                    interpretation_json=json.dumps(
                        interpretation.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    updated_at=now,
                )
            )

        if result.rowcount == 0:
            raise KeyError(quote_id)

    def get(self, quote_id: str) -> StoredQuoteRecord | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(QUOTE_TABLE).where(
                        QUOTE_TABLE.c.quote_id == quote_id
                    )
                )
                .mappings()
                .first()
            )

        if row is None:
            return None

        return StoredQuoteRecord(
            quote_id=row["quote_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            status=row["status"],
            selected_ranks=json.loads(
                row["selected_ranks_json"] or "[]"
            ),
            source=row["source"],
            client_name=row["client_name"],
            client_reference=row["client_reference"],
            notes=row["notes"],
            sent_at=row["sent_at"],
            parent_quote_id=row["parent_quote_id"],
            refreshed_quote_id=row["refreshed_quote_id"],
            agent_text=row["agent_text"],
            interpretation=(
                json.loads(row["interpretation_json"])
                if row["interpretation_json"]
                else None
            ),
            search_request=json.loads(
                row["search_request_json"]
            ),
            quote_response=json.loads(
                row["quote_response_json"]
            ),
        )

    def _ensure_artifacts_table(self) -> None:
        if self.dialect_name == "sqlite":
            QuoteArtifactRow.metadata.create_all(self.engine)

    def create_artifact(
        self,
        quote_id: str,
        *,
        artifact_type: str,
        title: str,
        selected_ranks: list[int] | None,
        content_type: str,
        content: str,
    ) -> dict:
        self._ensure_artifacts_table()
        created_at = _utc_now()

        ranks = sorted(
            {
                int(rank)
                for rank in (selected_ranks or [])
                if int(rank) > 0
            }
        )

        with self.engine.begin() as connection:
            result = connection.execute(
                insert(ARTIFACT_TABLE).values(
                    quote_id=quote_id,
                    artifact_type=artifact_type,
                    title=title,
                    selected_ranks_json=json.dumps(ranks),
                    content_type=content_type,
                    content=content,
                    created_at=created_at,
                )
            )
            artifact_id = int(
                result.inserted_primary_key[0]
            )

        return {
            "artifact_id": artifact_id,
            "quote_id": quote_id,
            "artifact_type": artifact_type,
            "title": title,
            "selected_ranks": ranks,
            "content_type": content_type,
            "content": content,
            "created_at": created_at,
        }

    def list_artifacts(self, quote_id: str) -> list[dict]:
        self._ensure_artifacts_table()

        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(ARTIFACT_TABLE)
                    .where(
                        ARTIFACT_TABLE.c.quote_id == quote_id
                    )
                    .order_by(
                        ARTIFACT_TABLE.c.artifact_id.desc()
                    )
                )
                .mappings()
                .all()
            )

        return [
            {
                "artifact_id": int(row["artifact_id"]),
                "quote_id": row["quote_id"],
                "artifact_type": row["artifact_type"],
                "title": row["title"],
                "selected_ranks": json.loads(
                    row["selected_ranks_json"] or "[]"
                ),
                "content_type": row["content_type"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def delete_artifact(
        self,
        quote_id: str,
        artifact_id: int,
    ) -> bool:
        self._ensure_artifacts_table()

        with self.engine.begin() as connection:
            result = connection.execute(
                delete(ARTIFACT_TABLE).where(
                    ARTIFACT_TABLE.c.quote_id == quote_id,
                    ARTIFACT_TABLE.c.artifact_id == artifact_id,
                )
            )

        return int(result.rowcount or 0) > 0

    def clear_artifacts(self, quote_id: str) -> int:
        self._ensure_artifacts_table()

        with self.engine.begin() as connection:
            result = connection.execute(
                delete(ARTIFACT_TABLE).where(
                    ARTIFACT_TABLE.c.quote_id == quote_id
                )
            )

        return int(result.rowcount or 0)

    def select(
        self,
        quote_id: str,
        ranks: list[int],
    ) -> QuoteSelectionResponse:
        record = self.get(quote_id)
        if record is None:
            raise KeyError(quote_id)

        available_ranks = {
            int(item.get("rank"))
            for item in (
                record.quote_response.get("options") or []
            )
            if item.get("rank") is not None
        }

        missing = [
            rank
            for rank in ranks
            if rank not in available_ranks
        ]
        if missing:
            raise ValueError(
                "Ranks inexistentes en la cotización: "
                + ", ".join(
                    str(rank)
                    for rank in missing
                )
            )

        normalized = sorted(set(ranks))
        now = _utc_now()

        with self.engine.begin() as connection:
            connection.execute(
                update(QUOTE_TABLE)
                .where(
                    QUOTE_TABLE.c.quote_id == quote_id
                )
                .values(
                    selected_ranks_json=json.dumps(
                        normalized
                    ),
                    status="selected",
                    updated_at=now,
                )
            )

        return QuoteSelectionResponse(
            quote_id=quote_id,
            status="selected",
            selected_ranks=normalized,
            selected_count=len(normalized),
        )

    def clear_selection(
        self,
        quote_id: str,
    ) -> QuoteSelectionResponse:
        if self.get(quote_id) is None:
            raise KeyError(quote_id)

        now = _utc_now()

        with self.engine.begin() as connection:
            connection.execute(
                update(QUOTE_TABLE)
                .where(
                    QUOTE_TABLE.c.quote_id == quote_id
                )
                .values(
                    selected_ranks_json="[]",
                    status="active",
                    updated_at=now,
                )
            )

        return QuoteSelectionResponse(
            quote_id=quote_id,
            status="active",
            selected_ranks=[],
            selected_count=0,
        )

    def update_workflow(
        self,
        quote_id: str,
        *,
        client_name: str | None = None,
        client_reference: str | None = None,
        notes: str | None = None,
        status: str | None = None,
    ) -> QuoteWorkflowResponse:
        record = self.get(quote_id)
        if record is None:
            raise KeyError(quote_id)

        allowed = {
            "active",
            "selected",
            "ready",
            "sent",
            "superseded",
        }
        if status is not None and status not in allowed:
            raise ValueError("Estado inválido.")

        now = _utc_now()
        sent_at = record.sent_at

        if status == "sent" and not sent_at:
            sent_at = now

        with self.engine.begin() as connection:
            connection.execute(
                update(QUOTE_TABLE)
                .where(
                    QUOTE_TABLE.c.quote_id == quote_id
                )
                .values(
                    client_name=(
                        client_name
                        if client_name is not None
                        else record.client_name
                    ),
                    client_reference=(
                        client_reference
                        if client_reference is not None
                        else record.client_reference
                    ),
                    notes=(
                        notes
                        if notes is not None
                        else record.notes
                    ),
                    status=(
                        status
                        if status is not None
                        else record.status
                    ),
                    sent_at=sent_at,
                    updated_at=now,
                )
            )

        updated = self.get(quote_id)
        assert updated is not None

        return QuoteWorkflowResponse(
            quote_id=updated.quote_id,
            status=updated.status,
            client_name=updated.client_name,
            client_reference=updated.client_reference,
            notes=updated.notes,
            sent_at=updated.sent_at,
            parent_quote_id=updated.parent_quote_id,
            refreshed_quote_id=updated.refreshed_quote_id,
        )

    def link_refresh(
        self,
        original_quote_id: str,
        refreshed_quote_id: str,
    ) -> None:
        now = _utc_now()

        with self.engine.begin() as connection:
            connection.execute(
                update(QUOTE_TABLE)
                .where(
                    QUOTE_TABLE.c.quote_id
                    == original_quote_id
                )
                .values(
                    refreshed_quote_id=refreshed_quote_id,
                    status="superseded",
                    updated_at=now,
                )
            )

    def list(
        self,
        limit: int = 20,
    ) -> list[StoredQuoteSummary]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(QUOTE_TABLE)
                    .order_by(
                        QUOTE_TABLE.c.created_at.desc()
                    )
                    .limit(limit)
                )
                .mappings()
                .all()
            )

        summaries: list[StoredQuoteSummary] = []

        for row in rows:
            request = json.loads(
                row["search_request_json"]
            )
            response = json.loads(
                row["quote_response_json"]
            )

            passengers = request.get("passengers") or []

            if passengers:
                passenger_count = sum(
                    int(item.get("quantity", 0))
                    for item in passengers
                )
            else:
                passenger_count = (
                    int(request.get("adults", 0))
                    + int(request.get("children", 0))
                    + int(request.get("infants", 0))
                )

            summaries.append(
                StoredQuoteSummary(
                    quote_id=row["quote_id"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    status=row["status"],
                    selected_ranks=json.loads(
                        row["selected_ranks_json"]
                        or "[]"
                    ),
                    source=row["source"],
                    client_name=row["client_name"],
                    client_reference=row[
                        "client_reference"
                    ],
                    parent_quote_id=row[
                        "parent_quote_id"
                    ],
                    sent_at=row["sent_at"],
                    origin=request.get("origin"),
                    destination=request.get(
                        "destination"
                    ),
                    departure_date=request.get(
                        "departure_date"
                    ),
                    return_date=request.get(
                        "return_date"
                    ),
                    passenger_count=passenger_count,
                    result_count=int(
                        response.get(
                            "result_count",
                            0,
                        )
                    ),
                )
            )

        return summaries


_repository: QuoteRepository | None = None


def get_quote_repository() -> QuoteRepository:
    global _repository

    desired_url = configured_database_url()

    if (
        _repository is None
        or _repository.database_url != desired_url
    ):
        if _repository is not None:
            _repository.close()

        _repository = QuoteRepository()

    return _repository


def reset_quote_repository_for_tests() -> None:
    global _repository

    if _repository is not None:
        _repository.close()

    _repository = None
