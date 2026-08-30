from pathlib import Path

from app.db.models import (
    BookingContactRow,
    BookingOfferRevisionRow,
    BookingPassengerRow,
    BookingRevalidationRow,
    BookingRow,
)


def test_booking_model_columns() -> None:
    assert [column.name for column in BookingRow.__table__.columns] == [
        "booking_id",
        "source_quote_id",
        "selected_rank",
        "environment",
        "status",
        "revalidation_status",
        "accepted_offer_revision_id",
        "revision",
        "client_request_id",
        "created_at",
        "updated_at",
        "abandoned_at",
    ]


def test_booking_offer_revision_columns() -> None:
    assert [column.name for column in BookingOfferRevisionRow.__table__.columns] == [
        "offer_revision_id",
        "booking_id",
        "revision_number",
        "source",
        "snapshot_json",
        "created_at",
        "accepted_at",
    ]


def test_booking_passenger_and_contact_tables_are_separate() -> None:
    assert "booking_id" in BookingPassengerRow.__table__.columns
    assert "passenger_type" in BookingPassengerRow.__table__.columns
    assert "booking_id" in BookingContactRow.__table__.columns
    assert "email" in BookingContactRow.__table__.columns
    assert "phone_number" in BookingContactRow.__table__.columns


def test_booking_revalidation_audit_table_exists() -> None:
    assert "status" in BookingRevalidationRow.__table__.columns
    assert "diff_json" in BookingRevalidationRow.__table__.columns
    assert "stale_at" in BookingRevalidationRow.__table__.columns


def test_booking_foundation_migration_exists() -> None:
    assert Path(
        "alembic/versions/20260826_04_booking_foundation.py"
    ).exists()
