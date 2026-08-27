from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.db.models import BookingRow
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
from app.sabre.create_booking import (
    SabreCreateBookingAmbiguousFailure,
    SabreCreateBookingResult,
    SabreCreateBookingSafeFailure,
)
from app.sabre.revalidation import SabreRevalidationResult
from app.services.booking_contact_service import BookingContactService
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_pnr_execution_service import (
    BookingPnrExecutionBindingError,
    BookingPnrExecutionReconciliationRequiredError,
    BookingPnrExecutionService,
)
from app.services.booking_revalidation_service import BookingRevalidationService
from app.services.booking_repository import BookingRepository


def _snapshot() -> BookingOfferSnapshot:
    segment = FlightSegment(
        marketing_carrier="AA",
        operating_carrier="AA",
        flight_number="900",
        departure_airport="EZE",
        arrival_airport="MIA",
        departure_country="AR",
        arrival_country="US",
        departure_at="2027-04-10T20:40:00",
        arrival_at="2027-04-11T05:00:00",
        booking_class="O",
        cabin_code="Y",
    )
    return BookingOfferSnapshot(
        source_quote_id="Q-EXEC",
        rank=1,
        fare_index=0,
        segments=[segment],
        fare=CommercialFare(
            cabin="economy",
            currency="USD",
            brand_name="MAIN",
            brand_code="MAIN",
            price_per_passenger=Decimal("500.00"),
            total_price=Decimal("500.00"),
            fare_basis_codes=["OLN0ATM1"],
            validating_carrier="AA",
        ),
        passenger_mix=[PassengerSpec(type=PassengerKind.ADULT, quantity=1)],
        legs=[SearchLeg(origin="EZE", destination="MIA", departure_date="2027-04-10")],
    )


def _candidate(snapshot: BookingOfferSnapshot) -> ItineraryOption:
    fare = FareOption(
        cabin=snapshot.fare.cabin,
        cabin_codes=["Y"],
        currency="USD",
        price_per_passenger=snapshot.fare.price_per_passenger,
        total_price=snapshot.fare.total_price,
        fare_basis_codes=list(snapshot.fare.fare_basis_codes),
        validating_carrier="AA",
        brand_name="MAIN",
        brand_code="MAIN",
    )
    return ItineraryOption(
        segments=snapshot.segments,
        fare=fare,
        fares_by_currency={"USD": fare},
        fare_options_by_currency={"USD": [fare]},
    )


class MatchProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def revalidate(self, snapshot, legs, *, environment):
        return SabreRevalidationResult(
            options=[_candidate(self.snapshot)],
            transaction_id="TX-EXEC",
            no_availability=False,
            messages=[],
        )


class CreateProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def create_booking(self, payload, *, environment):
        self.calls.append((payload, environment))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _ready(tmp_path):
    repository = BookingRepository(tmp_path / f"{uuid4().hex}.db")
    snapshot = _snapshot()
    booking = repository.create_initial(
        source_quote_id="Q-EXEC",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=snapshot,
    )
    BookingPassengerService(booking_repository=repository).update(
        booking.booking_id,
        BookingPassengersUpdateRequest(
            revision=1,
            passengers=[{
                "slot_index": 1,
                "given_name": "TEST",
                "surname": "BOOKING",
                "date_of_birth": "1985-04-15",
                "gender": "M",
            }],
        ),
    )
    BookingContactService(booking_repository=repository).update(
        booking.booking_id,
        BookingContactUpdateRequest(
            revision=2,
            name="Test Booking",
            email="test@example.com",
            phone_country_code="+54",
            phone_number="1100000000",
            preferred_channel="email",
        ),
    )
    await BookingRevalidationService(
        booking_repository=repository,
        provider=MatchProvider(snapshot),
    ).revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )
    ready = repository.get(booking.booking_id)
    assert ready is not None
    assert ready.status == BookingStatus.READY_TO_CREATE_PNR
    return repository, ready


@pytest.mark.asyncio
async def test_success_is_idempotent_and_marks_booking_pnr_created(tmp_path):
    repository, booking = await _ready(tmp_path)
    provider = CreateProvider([
        SabreCreateBookingResult(
            confirmation_id="ABC123",
            provider_reference="TX-CREATE",
        )
    ])
    service = BookingPnrExecutionService(
        booking_repository=repository,
        provider=provider,
    )
    request = BookingCreatePnrRequest(
        revision=booking.revision,
        client_request_id=uuid4(),
    )

    result = await service.execute(booking.booking_id, request)
    assert result.status == PnrAttemptStatus.SUCCEEDED
    assert result.request_fingerprint
    assert result.confirmation_id == "ABC123"
    assert len(provider.calls) == 1

    final_booking = repository.get(booking.booking_id)
    assert final_booking is not None
    assert final_booking.status == BookingStatus.PNR_CREATED
    assert final_booking.revision == booking.revision + 1

    retry = await service.execute(booking.booking_id, request)
    assert retry.confirmation_id == "ABC123"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_failed_safe_retries_same_attempt_only(tmp_path):
    repository, booking = await _ready(tmp_path)
    provider = CreateProvider([
        SabreCreateBookingSafeFailure("HTTP_400", "safe"),
        SabreCreateBookingResult(confirmation_id="SAFE01"),
    ])
    service = BookingPnrExecutionService(
        booking_repository=repository,
        provider=provider,
    )
    request = BookingCreatePnrRequest(
        revision=booking.revision,
        client_request_id=uuid4(),
    )

    failed = await service.execute(booking.booking_id, request)
    assert failed.status == PnrAttemptStatus.FAILED_SAFE
    assert failed.error_code == "HTTP_400"

    succeeded = await service.execute(booking.booking_id, request)
    assert succeeded.status == PnrAttemptStatus.SUCCEEDED
    assert succeeded.confirmation_id == "SAFE01"
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_ambiguous_never_resubmits(tmp_path):
    repository, booking = await _ready(tmp_path)
    provider = CreateProvider([
        SabreCreateBookingAmbiguousFailure("HTTP_500", "ambiguous"),
        SabreCreateBookingResult(confirmation_id="MUSTNOT"),
    ])
    service = BookingPnrExecutionService(
        booking_repository=repository,
        provider=provider,
    )
    request = BookingCreatePnrRequest(
        revision=booking.revision,
        client_request_id=uuid4(),
    )

    with pytest.raises(BookingPnrExecutionReconciliationRequiredError):
        await service.execute(booking.booking_id, request)

    attempt = BookingPnrAttemptService(
        booking_repository=repository
    ).get(booking.booking_id)
    assert attempt is not None
    assert attempt.status == PnrAttemptStatus.RECONCILIATION_REQUIRED

    with pytest.raises(BookingPnrExecutionReconciliationRequiredError):
        await service.execute(booking.booking_id, request)

    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_persisted_submitting_never_resubmits(tmp_path):
    repository, booking = await _ready(tmp_path)
    attempts = BookingPnrAttemptService(booking_repository=repository)
    request = BookingCreatePnrRequest(
        revision=booking.revision,
        client_request_id=uuid4(),
    )
    prepared = attempts.prepare(booking.booking_id, request)
    attempts.mark_submitting(
        prepared.pnr_attempt_id,
        request_fingerprint="a" * 64,
    )

    provider = CreateProvider([
        SabreCreateBookingResult(confirmation_id="NOPE")
    ])
    service = BookingPnrExecutionService(
        booking_repository=repository,
        attempt_service=attempts,
        provider=provider,
    )

    with pytest.raises(BookingPnrExecutionReconciliationRequiredError):
        await service.execute(booking.booking_id, request)

    assert provider.calls == []


@pytest.mark.asyncio
async def test_booking_change_after_prepare_prevents_send(tmp_path):
    repository, booking = await _ready(tmp_path)
    attempts = BookingPnrAttemptService(booking_repository=repository)
    request = BookingCreatePnrRequest(
        revision=booking.revision,
        client_request_id=uuid4(),
    )
    attempts.prepare(booking.booking_id, request)

    with repository.engine.begin() as connection:
        connection.execute(
            update(BookingRow.__table__)
            .where(BookingRow.__table__.c.booking_id == booking.booking_id)
            .values(revision=booking.revision + 1)
        )

    provider = CreateProvider([
        SabreCreateBookingResult(confirmation_id="NOPE")
    ])
    service = BookingPnrExecutionService(
        booking_repository=repository,
        attempt_service=attempts,
        provider=provider,
    )

    with pytest.raises(BookingPnrExecutionBindingError):
        await service.execute(booking.booking_id, request)

    assert provider.calls == []
    attempt = attempts.get(booking.booking_id)
    assert attempt is not None
    assert attempt.status == PnrAttemptStatus.PREPARED
