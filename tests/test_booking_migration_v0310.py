from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _alembic_config() -> Config:
    return Config("alembic.ini")


def _database_version(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def test_booking_foundation_alembic_upgrade_from_booking_draft(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "migration.db"
    database_url = (
        f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    monkeypatch.setenv("DATABASE_URL", database_url)

    config = _alembic_config()
    command.upgrade(config, "20260826_03")

    engine = create_engine(database_url)
    inspector = inspect(engine)

    assert inspector.has_table("quote_booking_drafts")
    assert not inspector.has_table("bookings")
    assert _database_version(engine) == "20260826_03"
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)

    expected_booking_tables = {
        "bookings",
        "booking_offer_revisions",
        "booking_passengers",
        "booking_contacts",
        "booking_revalidations",
        "booking_pnr_attempts",
        "booking_pnr_snapshots",
    }
    assert expected_booking_tables.issubset(
        set(inspector.get_table_names())
    )
    assert _database_version(engine) == "20260901_06"
    engine.dispose()
