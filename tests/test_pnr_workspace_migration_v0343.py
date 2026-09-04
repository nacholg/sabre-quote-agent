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


def test_pnr_workspace_snapshot_migration_from_pnr_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "pnr-workspace-migration.db"
    database_url = (
        f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = _config()
    command.upgrade(config, "20260827_05")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert not inspector.has_table("booking_pnr_snapshots")
    assert _version(engine) == "20260827_05"
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert inspector.has_table("booking_pnr_snapshots")
    assert _version(engine) == "20260904_07"

    columns = {
        item["name"]
        for item in inspector.get_columns("booking_pnr_snapshots")
    }
    assert {
        "booking_id",
        "confirmation_id",
        "provider",
        "environment",
        "retrieved_at",
        "snapshot_json",
    }.issubset(columns)

    pk = inspector.get_pk_constraint("booking_pnr_snapshots")
    assert pk["constrained_columns"] == ["booking_id"]

    indexes = {
        item["name"]
        for item in inspector.get_indexes("booking_pnr_snapshots")
    }
    assert "idx_booking_pnr_snapshots_confirmation" in indexes
    engine.dispose()
