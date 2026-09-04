from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_pricing_refresh_attempt_migration_upgrade(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "migration.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    table = "booking_pnr_pricing_refresh_attempts"
    assert table in inspector.get_table_names()

    columns = {
        item["name"]
        for item in inspector.get_columns(table)
    }
    assert {
        "pricing_refresh_attempt_id",
        "booking_id",
        "active_booking_id",
        "client_request_id",
        "expected_brand_code",
        "expected_currency",
        "expected_total",
        "status",
        "pricing_authority_id",
        "result_json",
        "created_at",
        "updated_at",
        "submitted_at",
        "completed_at",
    } <= columns

    unique_sets = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints(table)
    }
    assert ("active_booking_id",) in unique_sets
    assert ("client_request_id",) in unique_sets
