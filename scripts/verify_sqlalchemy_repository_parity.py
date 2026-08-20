from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import QuoteArtifactRow, QuoteRow
from app.services.quote_repository import QuoteRepository


def source_path() -> Path:
    return Path(
        os.getenv("QUOTE_DB_PATH", "data/quotes.db")
    ).expanduser().resolve()


def sqlite_backup(
    source: Path,
    destination: Path,
) -> None:
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)

    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def main() -> None:
    source = source_path()

    if not source.exists():
        raise SystemExit(f"No existe: {source}")

    legacy = sqlite3.connect(source)
    legacy.row_factory = sqlite3.Row

    try:
        legacy_quotes = legacy.execute(
            """
            SELECT *
            FROM quotes
            ORDER BY created_at DESC
            """
        ).fetchall()

        legacy_artifacts = legacy.execute(
            """
            SELECT *
            FROM quote_artifacts
            ORDER BY artifact_id DESC
            """
        ).fetchall()
    finally:
        legacy.close()

    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "quotes-copy.db"
        sqlite_backup(source, copy_path)

        repo = QuoteRepository(copy_path)

        summaries = repo.list(
            limit=max(1, len(legacy_quotes) + 10)
        )

        legacy_ids = [
            row["quote_id"]
            for row in legacy_quotes
        ]
        sqlalchemy_ids = [
            row.quote_id
            for row in summaries
        ]

        if legacy_ids != sqlalchemy_ids:
            raise SystemExit(
                "Quote ordering/IDs differ between sqlite3 "
                "and SQLAlchemy repository."
            )

        for legacy_row in legacy_quotes:
            quote_id = legacy_row["quote_id"]
            record = repo.get(quote_id)

            if record is None:
                raise SystemExit(
                    f"SQLAlchemy repo did not load {quote_id}"
                )

            if record.status != legacy_row["status"]:
                raise SystemExit(
                    f"Status mismatch for {quote_id}"
                )

            if record.selected_ranks != json.loads(
                legacy_row["selected_ranks_json"]
                or "[]"
            ):
                raise SystemExit(
                    f"Selected ranks mismatch for {quote_id}"
                )

            if record.search_request != json.loads(
                legacy_row["search_request_json"]
            ):
                raise SystemExit(
                    f"Search request mismatch for {quote_id}"
                )

            if record.quote_response != json.loads(
                legacy_row["quote_response_json"]
            ):
                raise SystemExit(
                    f"Quote response mismatch for {quote_id}"
                )

        legacy_artifacts_by_quote: dict[str, list] = {}

        for row in legacy_artifacts:
            legacy_artifacts_by_quote.setdefault(
                row["quote_id"],
                [],
            ).append(row)

        for quote_id, expected_rows in (
            legacy_artifacts_by_quote.items()
        ):
            actual_rows = repo.list_artifacts(quote_id)

            expected_ids = [
                int(row["artifact_id"])
                for row in expected_rows
            ]
            actual_ids = [
                int(row["artifact_id"])
                for row in actual_rows
            ]

            if expected_ids != actual_ids:
                raise SystemExit(
                    f"Artifact mismatch for {quote_id}"
                )

        with repo.engine.connect() as connection:
            quote_count = connection.execute(
                select(func.count()).select_from(
                    QuoteRow.__table__
                )
            ).scalar_one()

            artifact_count = connection.execute(
                select(func.count()).select_from(
                    QuoteArtifactRow.__table__
                )
            ).scalar_one()

        repo.close()

    print(f"SQLite source: {source}")
    print(
        f"Quotes:    sqlite3={len(legacy_quotes)} "
        f"SQLAlchemy={quote_count}"
    )
    print(
        f"Artifacts: sqlite3={len(legacy_artifacts)} "
        f"SQLAlchemy={artifact_count}"
    )
    print("Ordering: OK")
    print("Stored quote payloads: OK")
    print("Artifacts: OK")
    print()
    print("SQLAlchemy repository parity: OK")


if __name__ == "__main__":
    main()
