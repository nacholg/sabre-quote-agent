from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from app.db.models import BookingRevalidationRow, BookingRow
from app.main import app
from app.models.booking import (
    BookingOfferSnapshot,
    BookingPassengersUpdateRequest,
    BookingStatus,
    RevalidationStatus,
)
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FlightSegment
from app.models.quote_request import PassengerKind, PassengerSpec
from app.services.booking_passenger_service import (
    BookingPassengerLockedError,
    BookingPassengerService,
    BookingPassengerValidationError,
    BookingRevisionConflictError,
)
from app.services.booking_repository import (
    BookingRepository,
    reset_booking_repository_for_tests,
)


def _snapshot(
    passenger_mix: list[PassengerSpec] | None = None,
) -> BookingOfferSnapshot:
    return BookingOfferSnapshot(
        source_quote_id="Q-PAX-TEST",
        rank=1,
        fare_index=0,
        segments=[
            FlightSegment(
                marketing_carrier="AA",
                operating_carrier="AA",
                flight_number="900",
                departure_airport="EZE",
                arrival_airport="MIA",
                departure_country="AR",
                arrival_country="US",
                departure_at="2026-10-10T21:00:00-03:00",
                arrival_at="2026-10-11T05:30:00-04:00",
                booking_class="O",
                cabin_code="Y",
            )
        ],
        fare=CommercialFare(
            cabin="economy",
            currency="USD",
            brand_name="MAIN",
            brand_code="M",
            price_per_passenger=Decimal("500.00"),
            total_price=Decimal("1000.00"),
            fare_basis_codes=["OLN0ATM1"],
            validating_carrier="AA",
        ),
        passenger_mix=passenger_mix
        or [
            PassengerSpec(
                type=PassengerKind.ADULT,
                quantity=1,
            ),
            PassengerSpec(
                type=PassengerKind.CHILD,
                quantity=1,
                age=7,
            ),
        ],
    )


def _booking(
    tmp_path,
    passenger_mix: list[PassengerSpec] | None = None,
):
    repository = BookingRepository(tmp_path / "booking.db")
    booking = repository.create_initial(
        source_quote_id="Q-PAX-TEST",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=_snapshot(passenger_mix),
    )
    service = BookingPassengerService(
        booking_repository=repository,
    )
    return repository, service, booking


def _complete_two_passenger_payload(revision: int):
    return BookingPassengersUpdateRequest(
        revision=revision,
        passengers=[
            {
                "slot_index": 1,
                "given_name": "Juan",
                "middle_name": "Carlos",
                "surname": "Perez",
                "date_of_birth": "1985-04-15",
                "gender": "M",
            },
            {
                "slot_index": 2,
                "given_name": "Ana",
                "surname": "Perez",
                # 7 years old on 2026-10-10.
                "date_of_birth": "2018-11-01",
                "gender": "F",
            },
        ],
    )


def test_get_materializes_fixed_slots_from_offer_snapshot(tmp_path) -> None:
    _, service, booking = _booking(
        tmp_path,
        [
            PassengerSpec(type=PassengerKind.ADULT, quantity=2),
            PassengerSpec(
                type=PassengerKind.CHILD,
                quantity=1,
                age=7,
            ),
            PassengerSpec(type=PassengerKind.INFANT, quantity=1),
        ],
    )

    response = service.get(booking.booking_id)

    assert response.booking_revision == 1
    assert response.complete is False
    assert [
        (
            item.slot_index,
            item.passenger_type.value,
            item.quoted_age,
        )
        for item in response.passengers
    ] == [
        (1, "ADT", None),
        (2, "ADT", None),
        (3, "CHILD", 7),
        (4, "INF", None),
    ]


def test_passenger_update_persists_identity_and_increments_revision(
    tmp_path,
) -> None:
    repository, service, booking = _booking(tmp_path)

    response = service.update(
        booking.booking_id,
        _complete_two_passenger_payload(booking.revision),
    )

    assert response.complete is True
    assert response.booking_revision == 2
    assert response.passengers[0].given_name == "JUAN"
    assert response.passengers[0].middle_name == "CARLOS"
    assert response.passengers[0].surname == "PEREZ"
    assert response.passengers[1].quoted_age == 7

    reloaded = service.get(booking.booking_id)
    assert reloaded.model_dump() == response.model_dump()

    persisted_booking = repository.get(booking.booking_id)
    assert persisted_booking is not None
    assert persisted_booking.status == BookingStatus.DRAFT


def test_passenger_mix_cannot_add_remove_or_change_slots(tmp_path) -> None:
    _, service, booking = _booking(tmp_path)

    with pytest.raises(BookingPassengerValidationError):
        service.update(
            booking.booking_id,
            BookingPassengersUpdateRequest(
                revision=1,
                passengers=[
                    {
                        "slot_index": 1,
                        "given_name": "Juan",
                        "surname": "Perez",
                        "date_of_birth": "1985-04-15",
                    }
                ],
            ),
        )

    with pytest.raises(BookingPassengerValidationError):
        service.update(
            booking.booking_id,
            BookingPassengersUpdateRequest(
                revision=1,
                passengers=[
                    {"slot_index": 1},
                    {"slot_index": 99},
                ],
            ),
        )


def test_child_date_of_birth_must_match_quoted_age(tmp_path) -> None:
    _, service, booking = _booking(tmp_path)
    payload = _complete_two_passenger_payload(1)
    payload.passengers[1].date_of_birth = date(2019, 11, 1)

    with pytest.raises(
        BookingPassengerValidationError,
        match="cotizado con 7 años",
    ):
        service.update(booking.booking_id, payload)


def test_single_adult_is_automatically_associated_to_infant(
    tmp_path,
) -> None:
    _, service, booking = _booking(
        tmp_path,
        [
            PassengerSpec(type=PassengerKind.ADULT, quantity=1),
            PassengerSpec(type=PassengerKind.INFANT, quantity=1),
        ],
    )

    initial = service.get(booking.booking_id)

    assert initial.passengers[1].associated_adult_slot_index == 1


def test_revision_conflict_and_idempotent_network_retry(tmp_path) -> None:
    _, service, booking = _booking(tmp_path)
    payload = _complete_two_passenger_payload(1)

    first = service.update(booking.booking_id, payload)
    assert first.booking_revision == 2

    # A transport retry with the already-applied body is safe even if it
    # still carries revision 1.
    retried = service.update(booking.booking_id, payload)
    assert retried.booking_revision == 2

    changed = _complete_two_passenger_payload(1)
    changed.passengers[0].given_name = "Pedro"
    with pytest.raises(BookingRevisionConflictError):
        service.update(booking.booking_id, changed)


def test_material_passenger_edit_invalidates_revalidation(tmp_path) -> None:
    repository, service, booking = _booking(tmp_path)
    service.update(
        booking.booking_id,
        _complete_two_passenger_payload(1),
    )

    with repository.engine.begin() as connection:
        connection.execute(
            update(BookingRow.__table__)
            .where(
                BookingRow.__table__.c.booking_id == booking.booking_id
            )
            .values(
                status=BookingStatus.READY_TO_CREATE_PNR.value,
                revalidation_status=RevalidationStatus.MATCHED.value,
                revision=3,
            )
        )
        connection.execute(
            insert(BookingRevalidationRow.__table__).values(
                booking_id=booking.booking_id,
                provider="test",
                status=RevalidationStatus.MATCHED.value,
                checked_at="2026-08-26T20:00:00+00:00",
                source_offer_revision_id=booking.accepted_offer_revision_id,
                candidate_offer_revision_id=None,
                provider_reference="TEST",
                diff_json=None,
                error_code=None,
                error_message=None,
                stale_at=None,
            )
        )

    current = service.get(booking.booking_id)
    payload = BookingPassengersUpdateRequest(
        revision=current.booking_revision,
        passengers=[
            {
                "slot_index": passenger.slot_index,
                "given_name": (
                    "PEDRO"
                    if passenger.slot_index == 1
                    else passenger.given_name
                ),
                "middle_name": passenger.middle_name,
                "surname": passenger.surname,
                "date_of_birth": passenger.date_of_birth,
                "gender": passenger.gender,
                "associated_adult_slot_index": (
                    passenger.associated_adult_slot_index
                ),
            }
            for passenger in current.passengers
        ],
    )

    updated = service.update(booking.booking_id, payload)

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.status == BookingStatus.REVALIDATION_REQUIRED
    assert persisted.revalidation_status == RevalidationStatus.STALE
    assert updated.booking_revision == 4

    with repository.engine.connect() as connection:
        stale_at = connection.execute(
            select(BookingRevalidationRow.__table__.c.stale_at)
            .where(
                BookingRevalidationRow.__table__.c.booking_id
                == booking.booking_id
            )
        ).scalar_one()
    assert stale_at is not None


def test_abandoned_booking_passengers_are_locked(tmp_path) -> None:
    repository, service, booking = _booking(tmp_path)
    service.get(booking.booking_id)

    with repository.engine.begin() as connection:
        connection.execute(
            update(BookingRow.__table__)
            .where(
                BookingRow.__table__.c.booking_id == booking.booking_id
            )
            .values(status=BookingStatus.ABANDONED.value)
        )

    with pytest.raises(BookingPassengerLockedError):
        service.update(
            booking.booking_id,
            _complete_two_passenger_payload(1),
        )


def test_passenger_api_get_put_and_booking_does_not_embed_pii(
    tmp_path,
    monkeypatch,
) -> None:
    db = tmp_path / "api-booking.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("QUOTE_DB_PATH", str(db))
    reset_booking_repository_for_tests()

    repository = BookingRepository(db)
    booking = repository.create_initial(
        source_quote_id="Q-PAX-API",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=_snapshot(),
    )
    repository.close()
    reset_booking_repository_for_tests()

    with TestClient(app) as client:
        initial = client.get(
            f"/bookings/{booking.booking_id}/passengers"
        )
        assert initial.status_code == 200

        saved = client.put(
            f"/bookings/{booking.booking_id}/passengers",
            json=_complete_two_passenger_payload(1).model_dump(
                mode="json"
            ),
        )
        assert saved.status_code == 200
        assert saved.json()["complete"] is True
        assert saved.json()["booking_revision"] == 2

        booking_response = client.get(
            f"/bookings/{booking.booking_id}"
        )
        assert booking_response.status_code == 200
        booking_payload = booking_response.json()

    assert "given_name" not in booking_payload
    assert "surname" not in booking_payload

    reset_booking_repository_for_tests()
