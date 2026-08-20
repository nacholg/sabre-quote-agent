from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, func, insert, inspect, select, text
from sqlalchemy.engine import make_url

from app.db.database import normalize_database_url
from app.db.models import QuoteArtifactRow, QuoteRow


QUOTE_TABLE = QuoteRow.__table__
ARTIFACT_TABLE = QuoteArtifactRow.__table__


def source_path(value: str | None) -> Path:
    configured = value or os.getenv("QUOTE_DB_PATH") or "data/quotes.db"
    return Path(configured).expanduser().resolve()


def target_url(value: str | None) -> str:
    configured = value or os.getenv("DATABASE_URL")
    if not configured:
        raise SystemExit(
            "Falta DATABASE_URL. "
            "Configurá temporalmente la URL pública de Postgres."
        )

    normalized = normalize_database_url(configured)
    backend = make_url(normalized).get_backend_name()

    if backend != "postgresql":
        raise SystemExit(
            "DATABASE_URL no apunta a PostgreSQL. "
            f"Backend detectado: {backend}"
        )

    return normalized


def read_sqlite_rows(path: Path) -> tuple[list[dict], list[dict]]:
    if not path.exists():
        raise SystemExit(f"No existe SQLite: {path}")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    try:
        quotes = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM quotes ORDER BY created_at ASC"
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
    parser = argparse.ArgumentParser(
        description=(
            "Copia quotes y quote_artifacts desde SQLite a PostgreSQL. "
            "El destino debe estar vacío y migrado con Alembic."
        )
    )
    parser.add_argument(
        "--source",
        default=None,
        help="SQLite origen. Default: QUOTE_DB_PATH o data/quotes.db",
    )
    parser.add_argument(
        "--target-url",
        default=None,
        help="Postgres destino. Default: DATABASE_URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Sólo valida origen/destino y muestra conteos.",
    )
    args = parser.parse_args()

    source = source_path(args.source)
    target = target_url(args.target_url)

    quotes, artifacts = read_sqlite_rows(source)

    print(f"SQLite source: {source}")
    print(f"Quotes source: {len(quotes)}")
    print(f"Artifacts source: {len(artifacts)}")

    engine = create_engine(
        target,
        pool_pre_ping=True,
    )

    inspector = inspect(engine)
    missing = [
        name
        for name in ("quotes", "quote_artifacts")
        if not inspector.has_table(name)
    ]
    if missing:
        raise SystemExit(
            "Postgres no tiene el baseline aplicado. "
            "Ejecutá 'alembic upgrade head'. "
            f"Faltan: {', '.join(missing)}"
        )

    with engine.connect() as connection:
        target_quotes = connection.execute(
            select(func.count()).select_from(QUOTE_TABLE)
        ).scalar_one()
        target_artifacts = connection.execute(
            select(func.count()).select_from(ARTIFACT_TABLE)
        ).scalar_one()

    print(f"Quotes target before: {target_quotes}")
    print(f"Artifacts target before: {target_artifacts}")

    if target_quotes or target_artifacts:
        raise SystemExit(
            "El Postgres destino no está vacío. "
            "La migración se cancela para evitar duplicados."
        )

    if args.dry_run:
        print("Dry run: OK")
        engine.dispose()
        return

    with engine.begin() as connection:
        if quotes:
            connection.execute(
                insert(QUOTE_TABLE),
                quotes,
            )

        if artifacts:
            connection.execute(
                insert(ARTIFACT_TABLE),
                artifacts,
            )

        connection.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence(
                        'quote_artifacts',
                        'artifact_id'
                    ),
                    COALESCE(
                        (SELECT MAX(artifact_id) FROM quote_artifacts),
                        1
                    ),
                    EXISTS(
                        SELECT 1 FROM quote_artifacts
                    )
                )
                """
            )
        )

    with engine.connect() as connection:
        final_quotes = connection.execute(
            select(func.count()).select_from(QUOTE_TABLE)
        ).scalar_one()
        final_artifacts = connection.execute(
            select(func.count()).select_from(ARTIFACT_TABLE)
        ).scalar_one()

    engine.dispose()

    print()
    print(f"Quotes target after: {final_quotes}")
    print(f"Artifacts target after: {final_artifacts}")

    if final_quotes != len(quotes):
        raise SystemExit(
            "ERROR: cantidad de quotes distinta después de migrar."
        )

    if final_artifacts != len(artifacts):
        raise SystemExit(
            "ERROR: cantidad de artifacts distinta después de migrar."
        )

    print()
    print("SQLite -> PostgreSQL migration: OK")


if __name__ == "__main__":
    main()
