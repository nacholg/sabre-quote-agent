from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.booking import (
    BookingContactUpdateRequest,
    BookingCreatePnrRequest,
    BookingOfferSnapshot,
    BookingPassengersUpdateRequest,
    BookingRevalidationRequest,
    BookingStatus,
    PnrAttemptStatus,
)
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.models.quote_request import PassengerKind, PassengerSpec, SearchLeg
from app.sabre.revalidation import SabreRevalidationResult
from app.services.booking_contact_service import BookingContactService
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_pnr_attempt_service import (
    BookingPnrAttemptIdempotencyConflictError,
    BookingPnrAttemptRevisionConflictError,
    BookingPnrAttemptService,
    BookingPnrAttemptStateError,
)
from app.services.booking_pnr_state import (
    PnrAttemptTransitionError,
    require_pnr_attempt_transition,
)
from app.services.booking_revalidation_service import BookingRevalidationService
from app.services.booking_repository import BookingRepository


def _segment() -> FlightSegment:
    return FlightSegment(
        marketing_carrier="AA",
        operating_carrier="AA",
        flight_number="982",
        departure_airport="EZE",
        arrival_airport="MIA",
        departure_country="AR",
        arrival_country="US",
        departure_at="2027-02-10T10:10:00",
        arrival_at="2027-02-10T17:20:00",
        booking_class="O",
        cabin_code="Y",
    )


def _commercial_fare() -> CommercialFare:
    return CommercialFare(
        cabin="economy",
        currency="USD",
        brand_name="MAIN",
        brand_code="MAIN",
        price_per_passenger=Decimal("450.73"),
        total_price=Decimal("450.73"),
        fare_basis_codes=["OLN0ATM1"],
        validating_carrier="AA",
    )


def _snapshot() -> BookingOfferSnapshot:
    return BookingOfferSnapshot(
        source_quote_id="Q-PNR-FOUNDATION",
        rank=1,
        fare_index=0,
        segments=[_segment()],
        fare=_commercial_fare(),
        passenger_mix=[
            PassengerSpec(
                type=PassengerKind.ADULT,
                quantity=1,
            )
        ],
        legs=[
            SearchLeg(
                origin="EZE",
                destination="MIA",
                departure_date="2027-02-10",
                departure_time="10:10:00",
            )
        ],
    )


def _candidate() -> ItineraryOption:
    fare = FareOption(
        cabin="economy",
        cabin_codes=["Y"],
        currency="USD",
        price_per_passenger=Decimal("450.73"),
        total_price=Decimal("450.73"),
        fare_basis_codes=["OLN0ATM1"],
        validating_carrier="AA",
        brand_name="MAIN",
        brand_code="MAIN",
    )
    return ItineraryOption(
        segments=[_segment()],
        fare=fare,
        fares_by_currency={"USD": fare},
        fare_options_by_currency={"USD": [fare]},
    )


class MatchProvider:
    provider_name = "fake_sabre"

    async def revalidate(
        self,
        snapshot,
        legs,
        *,
        environment,
    ) -> SabreRevalidationResult:
        return SabreRevalidationResult(
            options=[_candidate()],
            transaction_id="TX-PNR-FOUNDATION",
            no_availability=False,
            messages=[],
        )


async def _matched_booking(tmp_path, *, suffix: str = "one"):
    repository = BookingRepository(
        tmp_path / f"pnr-foundation-{suffix}.db"
    )
    booking = repository.create_initial(
        source_quote_id=f"Q-PNR-{suffix}",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=_snapshot(),
    )

    BookingPassengerService(
        booking_repository=repository
    ).update(
        booking.booking_id,
        BookingPassengersUpdateRequest(
            revision=1,
            passengers=[
                {
                    "slot_index": 1,
                    "given_name": "TEST",
                    "surname": "PASSENGER",
                    "date_of_birth": "1985-04-15",
                    "gender": "M",
                }
            ],
        ),
    )

    BookingContactService(
        booking_repository=repository
    ).update(
        booking.booking_id,
        BookingContactUpdateRequest(
            revision=2,
            name="Test Passenger",
            email="test@example.com",
            phone_country_code="+54",
            phone_number="1155551234",
            preferred_channel="whatsapp",
        ),
    )

    result = await BookingRevalidationService(
        booking_repository=repository,
        provider=MatchProvider(),
    ).revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )
    assert result.status == BookingStatus.READY_TO_CREATE_PNR

    current = repository.get(booking.booking_id)
    assert current is not None
    assert current.revision == 4
    return repository, current


@pytest.mark.asyncio
async def test_prepare_pnr_attempt_binds_exact_matched_state(
    tmp_path,
) -> None:
    repository, booking = await _matched_booking(tmp_path)

    service = BookingPnrAttemptService(
        booking_repository=repository
    )
    request = BookingCreatePnrRequest(
        revision=booking.revision,
        client_request_id=uuid4(),
    )

    attempt = service.prepare(booking.booking_id, request)

    assert attempt.status == PnrAttemptStatus.PREPARED
    assert attempt.booking_revision == booking.revision
    assert (
        attempt.accepted_offer_revision_id
        == booking.accepted_offer_revision_id
    )
    assert attempt.revalidation_id >= 1
    assert attempt.environment == "cert"
    assert attempt.provider == "sabre_booking_management"
    assert attempt.confirmation_id is None
    assert attempt.submitted_at is None
    assert attempt.completed_at is None

    # Part 1 is persistence only: preparing cannot mutate Booking state.
    after = repository.get(booking.booking_id)
    assert after is not None
    assert after.revision == booking.revision
    assert after.status == BookingStatus.READY_TO_CREATE_PNR


@pytest.mark.asyncio
async def test_exact_prepare_retry_is_idempotent(tmp_path) -> None:
    repository, booking = await _matched_booking(tmp_path)
    service = BookingPnrAttemptService(
        booking_repository=repository
    )
    request = BookingCreatePnrRequest(
        revision=booking.revision,
        client_request_id=uuid4(),
    )

    first = service.prepare(booking.booking_id, request)
    second = service.prepare(booking.booking_id, request)

    assert first.pnr_attempt_id == second.pnr_attempt_id
    assert first.client_request_id == second.client_request_id


@pytest.mark.asyncio
async def test_one_booking_cannot_get_second_attempt_with_new_key(
    tmp_path,
) -> None:
    repository, booking = await _matched_booking(tmp_path)
    service = BookingPnrAttemptService(
        booking_repository=repository
    )

    service.prepare(
        booking.booking_id,
        BookingCreatePnrRequest(
            revision=booking.revision,
            client_request_id=uuid4(),
        ),
    )

    with pytest.raises(
        BookingPnrAttemptIdempotencyConflictError,
        match="ya tiene un intento",
    ):
        service.prepare(
            booking.booking_id,
            BookingCreatePnrRequest(
                revision=booking.revision,
                client_request_id=uuid4(),
            ),
        )


@pytest.mark.asyncio
async def test_same_key_cannot_bind_to_different_booking(
    tmp_path,
) -> None:
    # Use the same SQLite repository so the global unique key is meaningful.
    repository, first_booking = await _matched_booking(
        tmp_path,
        suffix="shared",
    )
    key = uuid4()
    service = BookingPnrAttemptService(
        booking_repository=repository
    )
    service.prepare(
        first_booking.booking_id,
        BookingCreatePnrRequest(
            revision=first_booking.revision,
            client_request_id=key,
        ),
    )

    # Create another ready Booking in the same repository.
    second = repository.create_initial(
        source_quote_id="Q-PNR-SECOND",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=_snapshot(),
    )
    BookingPassengerService(
        booking_repository=repository
    ).update(
        second.booking_id,
        BookingPassengersUpdateRequest(
            revision=1,
            passengers=[
                {
                    "slot_index": 1,
                    "given_name": "SECOND",
                    "surname": "PASSENGER",
                    "date_of_birth": "1980-01-01",
                    "gender": "F",
                }
            ],
        ),
    )
    BookingContactService(
        booking_repository=repository
    ).update(
        second.booking_id,
        BookingContactUpdateRequest(
            revision=2,
            name="Second Passenger",
            email="second@example.com",
            phone_country_code="+54",
            phone_number="1155550000",
            preferred_channel="email",
        ),
    )
    await BookingRevalidationService(
        booking_repository=repository,
        provider=MatchProvider(),
    ).revalidate(
        second.booking_id,
        BookingRevalidationRequest(revision=3),
    )
    second = repository.get(second.booking_id)
    assert second is not None

    with pytest.raises(
        BookingPnrAttemptIdempotencyConflictError,
        match="otro intento",
    ):
        service.prepare(
            second.booking_id,
            BookingCreatePnrRequest(
                revision=second.revision,
                client_request_id=key,
            ),
        )


@pytest.mark.asyncio
async def test_stale_booking_revision_cannot_prepare_attempt(
    tmp_path,
) -> None:
    repository, booking = await _matched_booking(tmp_path)
    service = BookingPnrAttemptService(
        booking_repository=repository
    )

    with pytest.raises(
        BookingPnrAttemptRevisionConflictError,
        match="Recargá",
    ):
        service.prepare(
            booking.booking_id,
            BookingCreatePnrRequest(
                revision=booking.revision - 1,
                client_request_id=uuid4(),
            ),
        )


def test_draft_booking_fails_server_readiness_gate(tmp_path) -> None:
    repository = BookingRepository(tmp_path / "draft.db")
    booking = repository.create_initial(
        source_quote_id="Q-DRAFT",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=_snapshot(),
    )

    with pytest.raises(
        BookingPnrAttemptStateError,
        match="readiness gate",
    ):
        BookingPnrAttemptService(
            booking_repository=repository
        ).prepare(
            booking.booking_id,
            BookingCreatePnrRequest(
                revision=booking.revision,
                client_request_id=uuid4(),
            ),
        )


def test_pnr_attempt_state_machine_preserves_ambiguous_outcome() -> None:
    assert require_pnr_attempt_transition(
        PnrAttemptStatus.PREPARED,
        PnrAttemptStatus.SUBMITTING,
    ) == PnrAttemptStatus.SUBMITTING

    assert require_pnr_attempt_transition(
        PnrAttemptStatus.SUBMITTING,
        PnrAttemptStatus.RECONCILIATION_REQUIRED,
    ) == PnrAttemptStatus.RECONCILIATION_REQUIRED

    with pytest.raises(PnrAttemptTransitionError):
        require_pnr_attempt_transition(
            PnrAttemptStatus.RECONCILIATION_REQUIRED,
            PnrAttemptStatus.SUBMITTING,
        )

    assert require_pnr_attempt_transition(
        PnrAttemptStatus.RECONCILIATION_REQUIRED,
        PnrAttemptStatus.FAILED_SAFE,
    ) == PnrAttemptStatus.FAILED_SAFE

    assert require_pnr_attempt_transition(
        PnrAttemptStatus.FAILED_SAFE,
        PnrAttemptStatus.SUBMITTING,
    ) == PnrAttemptStatus.SUBMITTING


def test_part1_contains_no_sabre_create_booking_call() -> None:
    paths = [
        Path("app/services/booking_pnr_attempt_service.py"),
        Path("app/services/booking_pnr_state.py"),
    ]
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in paths
    ).lower()

    assert "/trip/orders/createbooking" not in combined
    assert "sabreclient" not in combined
    assert ".post(" not in combined
