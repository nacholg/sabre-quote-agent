from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url

from app.db.database import normalize_database_url
from app.db.models import QuoteArtifactRow, QuoteRow


QUOTE_TABLE = QuoteRow.__table__
ARTIFACT_TABLE = QuoteArtifactRow.__table__

QUOTE_COLUMNS = [
    column.name
    for column in QUOTE_TABLE.columns
]

ARTIFACT_COLUMNS = [
    column.name
    for column in ARTIFACT_TABLE.columns
]


def canonical_hash(rows: list[dict], columns: list[str]) -> str:
    payload = [
        {
            column: row.get(column)
            for column in columns
        }
        for row in rows
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_sqlite(path: Path) -> tuple[list[dict], list[dict]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    try:
        quotes = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM quotes ORDER BY quote_id ASC"
            ).fetchall()
        ]
        artifacts = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM quote_artifacts ORDER BY artifact_id ASC"
            ).fetchall()
        ]
    finally:
        conn.close()

    return quotes, artifacts


def main() -> None:
    sqlite_path = Path(
        os.getenv("QUOTE_DB_PATH", "data/quotes.db")
    ).expanduser().resolve()

    if not sqlite_path.exists():
        raise SystemExit(f"No existe SQLite: {sqlite_path}")

    configured = os.getenv("DATABASE_URL")
    if not configured:
        raise SystemExit("Falta DATABASE_URL.")

    url = normalize_database_url(configured)
    if make_url(url).get_backend_name() != "postgresql":
        raise SystemExit("DATABASE_URL debe apuntar a PostgreSQL.")

    sqlite_quotes, sqlite_artifacts = read_sqlite(sqlite_path)

    engine = create_engine(url, pool_pre_ping=True)

    with engine.connect() as connection:
        postgres_quotes = [
            dict(row)
            for row in connection.execute(
                select(QUOTE_TABLE).order_by(
                    QUOTE_TABLE.c.quote_id.asc()
                )
            ).mappings().all()
        ]

        postgres_artifacts = [
            dict(row)
            for row in connection.execute(
                select(ARTIFACT_TABLE).order_by(
                    ARTIFACT_TABLE.c.artifact_id.asc()
                )
            ).mappings().all()
        ]

    engine.dispose()

    sqlite_quote_hash = canonical_hash(
        sorted(
            sqlite_quotes,
            key=lambda row: row["quote_id"],
        ),
        QUOTE_COLUMNS,
    )
    postgres_quote_hash = canonical_hash(
        postgres_quotes,
        QUOTE_COLUMNS,
    )

    sqlite_artifact_hash = canonical_hash(
        sqlite_artifacts,
        ARTIFACT_COLUMNS,
    )
    postgres_artifact_hash = canonical_hash(
        postgres_artifacts,
        ARTIFACT_COLUMNS,
    )

    print(
        f"Quotes:    SQLite={len(sqlite_quotes)} "
        f"Postgres={len(postgres_quotes)}"
    )
    print(
        f"Artifacts: SQLite={len(sqlite_artifacts)} "
        f"Postgres={len(postgres_artifacts)}"
    )
    print()
    print(f"Quotes SHA256 SQLite:   {sqlite_quote_hash}")
    print(f"Quotes SHA256 Postgres: {postgres_quote_hash}")
    print()
    print(f"Artifacts SHA256 SQLite:   {sqlite_artifact_hash}")
    print(f"Artifacts SHA256 Postgres: {postgres_artifact_hash}")

    if sqlite_quote_hash != postgres_quote_hash:
        raise SystemExit("ERROR: quotes no son idénticos.")

    if sqlite_artifact_hash != postgres_artifact_hash:
        raise SystemExit("ERROR: artifacts no son idénticos.")

    print()
    print("SQLite/PostgreSQL data parity: OK")


if __name__ == "__main__":
    main()
