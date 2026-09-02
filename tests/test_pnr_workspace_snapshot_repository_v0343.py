from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import BookingPnrSnapshotRow
from app.models.pnr_workspace import (
    PnrContact,
    PnrPassenger,
    PnrPriceQuote,
    PnrSegment,
    PnrSnapshot,
)
from app.services.booking_repository import BookingRepository
from app.services.pnr_workspace_snapshot_repository import (
    PnrWorkspaceSnapshotRepository,
)


def _snapshot(total: str) -> PnrSnapshot:
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        passengers=[
            PnrPassenger(
                name_number="01.01",
                passenger_type="ADT",
                given_name="TEST",
                surname="PASSENGER",
            )
        ],
        contacts=[
            PnrContact(
                kind="email",
                value="test@example.com",
            )
        ],
        segments=[
            PnrSegment(
                segment_number="1",
                marketing_carrier="AA",
                flight_number="900",
                origin="EZE",
                destination="MIA",
                booking_class="S",
                status="HK",
            )
        ],
        price_quotes=[
            PnrPriceQuote(
                record_number="1",
                total_amount=Decimal(total),
                total_currency="USD",
            )
        ],
    )


def test_snapshot_repository_upserts_one_latest_record(tmp_path) -> None:
    booking_repository = BookingRepository(
        db_path=tmp_path / "workspace.db"
    )
    repository = PnrWorkspaceSnapshotRepository(
        booking_repository=booking_repository
    )

    first = repository.save(
        booking_id="B-TEST",
        confirmation_id="OVFOTM",
        provider="sabre_travel_itinerary_read",
        environment="cert",
        snapshot=_snapshot("781.33"),
    )
    second = repository.save(
        booking_id="B-TEST",
        confirmation_id="OVFOTM",
        provider="sabre_travel_itinerary_read",
        environment="cert",
        snapshot=_snapshot("800.00"),
    )

    assert first.booking_id == "B-TEST"
    assert second.snapshot.price_quotes[0].total_amount == Decimal(
        "800.00"
    )

    latest = repository.latest("B-TEST")
    assert latest is not None
    assert latest.confirmation_id == "OVFOTM"
    assert latest.snapshot.passengers[0].surname == "PASSENGER"

    table = BookingPnrSnapshotRow.__table__
    with booking_repository.engine.connect() as connection:
        count = connection.execute(
            select(func.count()).select_from(table)
        ).scalar_one()
    assert count == 1

    booking_repository.close()
