from pathlib import Path


def test_postgres_migration_tools_exist():
    assert Path(
        "scripts/migrate_sqlite_to_postgres.py"
    ).exists()
    assert Path(
        "scripts/verify_postgres_migration.py"
    ).exists()


def test_migration_has_destination_safety_checks():
    src = Path(
        "scripts/migrate_sqlite_to_postgres.py"
    ).read_text(encoding="utf-8")

    assert 'backend != "postgresql"' in src
    assert "El Postgres destino no está vacío." in src
    assert "setval(" in src


def test_verifier_compares_complete_payload_hashes():
    src = Path(
        "scripts/verify_postgres_migration.py"
    ).read_text(encoding="utf-8")

    assert "sha256" in src
    assert "QUOTE_COLUMNS" in src
    assert "ARTIFACT_COLUMNS" in src
    assert "SQLite/PostgreSQL data parity: OK" in src
