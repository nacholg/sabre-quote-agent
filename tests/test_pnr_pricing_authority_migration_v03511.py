from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_pricing_authority_migration_upgrade(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "migration.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert "booking_pnr_pricing_authorities" in inspector.get_table_names()
    columns = {
        item["name"]
        for item in inspector.get_columns(
            "booking_pnr_pricing_authorities"
        )
    }
    assert {
        "pricing_authority_id",
        "booking_id",
        "confirmation_id",
        "price_quote_record_numbers_json",
        "brand_code",
        "original_total",
        "current_total",
        "price_difference",
        "verified_at",
    } <= columns
