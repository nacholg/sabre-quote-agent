from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config() -> Config:
    return Config("alembic.ini")


def _version(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def test_pnr_attempt_migration_from_booking_foundation(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "pnr-attempt-migration.db"
    database_url = (
        f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = _config()
    command.upgrade(config, "20260826_04")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not inspector.has_table("booking_pnr_attempts")
    assert _version(engine) == "20260826_04"
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert inspector.has_table("booking_pnr_attempts")
    assert inspector.has_table("booking_pnr_snapshots")
    assert _version(engine) == "20260904_07"

    columns = {
        item["name"]
        for item in inspector.get_columns("booking_pnr_attempts")
    }
    assert {
        "pnr_attempt_id",
        "booking_id",
        "client_request_id",
        "booking_revision",
        "accepted_offer_revision_id",
        "revalidation_id",
        "environment",
        "provider",
        "status",
        "confirmation_id",
        "provider_reference",
        "request_fingerprint",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "submitted_at",
        "completed_at",
    }.issubset(columns)

    unique_sets = {
        tuple(sorted(item["column_names"]))
        for item in inspector.get_unique_constraints(
            "booking_pnr_attempts"
        )
    }
    assert ("booking_id",) in unique_sets
    assert ("client_request_id",) in unique_sets
    engine.dispose()
