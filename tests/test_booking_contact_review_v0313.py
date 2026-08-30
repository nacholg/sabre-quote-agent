from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, update

from app.db.models import BookingRevalidationRow, BookingRow
from app.main import app
from app.models.booking import (
    BookingContactUpdateRequest,
    BookingOfferSnapshot,
    BookingPassengersUpdateRequest,
    BookingStatus,
    RevalidationStatus,
)
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FlightSegment
from app.models.quote_request import PassengerKind, PassengerSpec
from app.services.booking_contact_service import (
    BookingContactService,
    BookingContactValidationError,
)
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_repository import (
    BookingRepository,
    reset_booking_repository_for_tests,
)
from app.services.booking_review_service import BookingReviewService


def _snapshot() -> BookingOfferSnapshot:
    return BookingOfferSnapshot(
        source_quote_id="Q-CONTACT-TEST",
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
            total_price=Decimal("500.00"),
            fare_basis_codes=["OLN0ATM1"],
            validating_carrier="AA",
        ),
        passenger_mix=[
            PassengerSpec(
                type=PassengerKind.ADULT,
                quantity=1,
            )
        ],
    )


def _services(tmp_path):
    repository = BookingRepository(tmp_path / "contact.db")
    booking = repository.create_initial(
        source_quote_id="Q-CONTACT-TEST",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=_snapshot(),
    )
    return (
        repository,
        BookingPassengerService(booking_repository=repository),
        BookingContactService(booking_repository=repository),
        BookingReviewService(booking_repository=repository),
        booking,
    )


def _passenger_payload(revision: int) -> BookingPassengersUpdateRequest:
    return BookingPassengersUpdateRequest(
        revision=revision,
        passengers=[
            {
                "slot_index": 1,
                "given_name": "Test",
                "middle_name": "User",
                "surname": "Passenger",
                "date_of_birth": "1985-04-15",
                "gender": "M",
            }
        ],
    )


def _contact_payload(revision: int) -> BookingContactUpdateRequest:
    return BookingContactUpdateRequest(
        revision=revision,
        name="Test Passenger",
        email=" TEST@EXAMPLE.COM ",
        phone_country_code="+54",
        phone_number="11 5555-1234",
        preferred_channel="whatsapp",
    )


def test_contact_get_starts_empty_and_does_not_change_revision(
    tmp_path,
) -> None:
    _, _, contact_service, _, booking = _services(tmp_path)

    contact = contact_service.get(booking.booking_id)

    assert contact.booking_revision == 1
    assert contact.complete is False
    assert contact.name is None
    assert contact.email is None


def test_complete_contact_without_passengers_keeps_booking_draft(
    tmp_path,
) -> None:
    repository, _, contact_service, _, booking = _services(tmp_path)

    contact = contact_service.update(
        booking.booking_id,
        _contact_payload(1),
    )

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.status == BookingStatus.DRAFT
    assert persisted.revision == 2
    assert contact.complete is True
    assert contact.email == "test@example.com"
    assert contact.phone_country_code == "+54"
    assert contact.phone_number == "1155551234"


def test_passengers_plus_contact_promotes_ready_for_review(tmp_path) -> None:
    repository, passenger_service, contact_service, review_service, booking = (
        _services(tmp_path)
    )

    passengers = passenger_service.update(
        booking.booking_id,
        _passenger_payload(1),
    )
    assert passengers.complete is True
    assert passengers.booking_revision == 2

    contact = contact_service.update(
        booking.booking_id,
        _contact_payload(2),
    )
    assert contact.complete is True
    assert contact.booking_revision == 3

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.status == BookingStatus.READY_FOR_REVIEW

    review = review_service.get(booking.booking_id)
    assert review.ready_for_review is True
    assert review.passengers_complete is True
    assert review.contact_complete is True
    assert review.offer_revision.snapshot.fare.brand_name == "MAIN"
    assert review.passengers[0].given_name == "TEST"
    assert review.contact.email == "test@example.com"


def test_clearing_contact_from_ready_for_review_returns_to_draft(
    tmp_path,
) -> None:
    repository, passenger_service, contact_service, _, booking = _services(
        tmp_path
    )
    passenger_service.update(
        booking.booking_id,
        _passenger_payload(1),
    )
    contact_service.update(
        booking.booking_id,
        _contact_payload(2),
    )

    incomplete = BookingContactUpdateRequest(
        revision=3,
        name="Test Passenger",
        email="test@example.com",
        phone_country_code=None,
        phone_number=None,
        preferred_channel="email",
    )
    saved = contact_service.update(
        booking.booking_id,
        incomplete,
    )

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.status == BookingStatus.DRAFT
    assert saved.complete is False
    assert saved.booking_revision == 4


def test_contact_rejects_invalid_email_and_partial_phone(tmp_path) -> None:
    _, _, contact_service, _, booking = _services(tmp_path)

    with pytest.raises(
        BookingContactValidationError,
        match="email",
    ):
        contact_service.update(
            booking.booking_id,
            BookingContactUpdateRequest(
                revision=1,
                name="Test",
                email="not-an-email",
                phone_country_code="+54",
                phone_number="1155551234",
            ),
        )

    with pytest.raises(
        BookingContactValidationError,
        match="juntos",
    ):
        contact_service.update(
            booking.booking_id,
            BookingContactUpdateRequest(
                revision=1,
                name="Test",
                email="test@example.com",
                phone_country_code="+54",
                phone_number=None,
            ),
        )


def test_contact_same_payload_retry_is_idempotent(tmp_path) -> None:
    repository, _, contact_service, _, booking = _services(tmp_path)
    payload = _contact_payload(1)

    first = contact_service.update(booking.booking_id, payload)
    second = contact_service.update(booking.booking_id, payload)

    assert first.booking_revision == 2
    assert second.booking_revision == 2
    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.revision == 2


def test_contact_edit_after_revalidation_marks_booking_stale(
    tmp_path,
) -> None:
    repository, passenger_service, contact_service, _, booking = _services(
        tmp_path
    )
    passenger_service.update(
        booking.booking_id,
        _passenger_payload(1),
    )
    contact_service.update(
        booking.booking_id,
        _contact_payload(2),
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
                revision=4,
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

    changed = _contact_payload(4)
    changed.phone_number = "1155559999"
    saved = contact_service.update(
        booking.booking_id,
        changed,
    )

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.status == BookingStatus.REVALIDATION_REQUIRED
    assert persisted.revalidation_status == RevalidationStatus.STALE
    assert saved.booking_revision == 5


def test_contact_and_review_api(tmp_path, monkeypatch) -> None:
    db = tmp_path / "contact-api.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("QUOTE_DB_PATH", str(db))
    reset_booking_repository_for_tests()

    repository = BookingRepository(db)
    booking = repository.create_initial(
        source_quote_id="Q-CONTACT-API",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=_snapshot(),
    )
    repository.close()
    reset_booking_repository_for_tests()

    with TestClient(app) as client:
        passengers = client.put(
            f"/bookings/{booking.booking_id}/passengers",
            json=_passenger_payload(1).model_dump(mode="json"),
        )
        assert passengers.status_code == 200

        contact = client.get(
            f"/bookings/{booking.booking_id}/contact"
        )
        assert contact.status_code == 200
        assert contact.json()["complete"] is False

        saved = client.put(
            f"/bookings/{booking.booking_id}/contact",
            json=_contact_payload(2).model_dump(mode="json"),
        )
        assert saved.status_code == 200
        assert saved.json()["complete"] is True

        review = client.get(
            f"/bookings/{booking.booking_id}/review"
        )

    assert review.status_code == 200
    payload = review.json()
    assert payload["status"] == "ready_for_review"
    assert payload["ready_for_review"] is True
    assert payload["contact"]["email"] == "test@example.com"
    assert payload["passengers"][0]["given_name"] == "TEST"

    reset_booking_repository_for_tests()
