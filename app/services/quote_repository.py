from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, delete, insert, inspect, or_, select, update
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
    QuoteVersionHistory,
    QuoteVersionItem,
)


QUOTE_TABLE = QuoteRow.__table__
ARTIFACT_TABLE = QuoteArtifactRow.__table__


class QuoteVersionConflictError(RuntimeError):
    """Raised when a historical quote version is used for a mutable action."""


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

        response_data = response_copy.model_dump(mode="json")
        if response_copy.candidate_options:
            response_data["_candidate_options"] = [
                item.model_dump(mode="json")
                for item in response_copy.candidate_options
            ]

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
            "quote_response_json": json.dumps(
                response_data,
                ensure_ascii=False,
            ),
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

    def assert_latest(
        self,
        quote_id: str,
    ) -> StoredQuoteRecord:
        """Return quote only when it is the latest mutable version."""
        record = self.get(quote_id)
        if record is None:
            raise KeyError(quote_id)

        if record.refreshed_quote_id or record.status == "superseded":
            latest = record.refreshed_quote_id or "una versión posterior"
            raise QuoteVersionConflictError(
                "La cotización es una versión histórica y es de solo lectura. "
                f"Usá la versión actual ({latest}) para modificarla."
            )

        return record

    def select(
        self,
        quote_id: str,
        ranks: list[int],
    ) -> QuoteSelectionResponse:
        record = self.assert_latest(quote_id)

        stored_options = list(
            record.quote_response.get("options") or []
        ) + list(
            record.quote_response.get("_candidate_options") or []
        )
        available_ranks = {
            int(item.get("rank"))
            for item in stored_options
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
        self.assert_latest(quote_id)

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
        record = self.assert_latest(quote_id)

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

    def version_history(
        self,
        quote_id: str,
    ) -> QuoteVersionHistory:
        """Return the complete refresh/reprice lineage for one quote.

        Versioning uses the existing parent_quote_id/refreshed_quote_id links,
        so this is portable across SQLite and PostgreSQL and needs no schema
        migration.
        """
        current = self.get(quote_id)
        if current is None:
            raise KeyError(quote_id)

        # Walk backwards to the root.
        root = current
        seen: set[str] = set()
        while root.parent_quote_id:
            if root.quote_id in seen:
                raise RuntimeError(
                    f"Ciclo detectado en versiones de cotización: {root.quote_id}"
                )
            seen.add(root.quote_id)

            parent = self.get(root.parent_quote_id)
            if parent is None:
                raise RuntimeError(
                    "Cadena de versiones incompleta: "
                    f"no existe parent_quote_id={root.parent_quote_id}"
                )
            root = parent

        # Walk forwards from the root to the latest quote.
        chain: list[StoredQuoteRecord] = []
        cursor = root
        seen.clear()

        while True:
            if cursor.quote_id in seen:
                raise RuntimeError(
                    f"Ciclo detectado en versiones de cotización: {cursor.quote_id}"
                )
            seen.add(cursor.quote_id)
            chain.append(cursor)

            if not cursor.refreshed_quote_id:
                break

            next_quote = self.get(cursor.refreshed_quote_id)
            if next_quote is None:
                raise RuntimeError(
                    "Cadena de versiones incompleta: "
                    f"no existe refreshed_quote_id={cursor.refreshed_quote_id}"
                )
            cursor = next_quote

        quote_ids = [item.quote_id for item in chain]
        try:
            current_index = quote_ids.index(quote_id)
        except ValueError as exc:
            raise RuntimeError(
                f"La cotización {quote_id} no pertenece a su propia cadena de versiones."
            ) from exc

        latest_quote_id = chain[-1].quote_id
        versions = [
            QuoteVersionItem(
                quote_id=item.quote_id,
                version=index + 1,
                status=item.status,
                source=item.source,
                created_at=item.created_at,
                updated_at=item.updated_at,
                selected_ranks=item.selected_ranks,
                sent_at=item.sent_at,
                is_current=item.quote_id == quote_id,
                is_latest=item.quote_id == latest_quote_id,
            )
            for index, item in enumerate(chain)
        ]

        return QuoteVersionHistory(
            quote_id=quote_id,
            root_quote_id=chain[0].quote_id,
            latest_quote_id=latest_quote_id,
            current_version=current_index + 1,
            total_versions=len(chain),
            is_latest=quote_id == latest_quote_id,
            versions=versions,
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
        *,
        search: str | None = None,
    ) -> list[StoredQuoteSummary]:
        statement = select(QUOTE_TABLE)

        # Portable SQLite/PostgreSQL search. Every whitespace-separated token
        # must match at least one searchable field. search_request_json is TEXT,
        # so route/date terms remain searchable without backend-specific JSON SQL.
        tokens = [
            token
            for token in (search or "").strip().split()
            if token
        ]
        searchable_columns = (
            QUOTE_TABLE.c.quote_id,
            QUOTE_TABLE.c.client_name,
            QUOTE_TABLE.c.client_reference,
            QUOTE_TABLE.c.status,
            QUOTE_TABLE.c.source,
            QUOTE_TABLE.c.agent_text,
            QUOTE_TABLE.c.notes,
            QUOTE_TABLE.c.search_request_json,
        )

        for token in tokens:
            pattern = f"%{token}%"
            statement = statement.where(
                or_(
                    *[
                        column.ilike(pattern)
                        for column in searchable_columns
                    ]
                )
            )

        statement = (
            statement
            .order_by(QUOTE_TABLE.c.created_at.desc())
            .limit(limit)
        )

        with self.engine.connect() as connection:
            rows = (
                connection.execute(statement)
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
