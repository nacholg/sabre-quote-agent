from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.booking import (
    BookingContactUpdateRequest,
    BookingOfferSnapshot,
    BookingPassengersUpdateRequest,
    BookingRevalidationRequest,
    BookingStatus,
    RevalidationStatus,
)
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.models.quote_request import PassengerKind, PassengerSpec, SearchLeg
from app.sabre.revalidation import SabreRevalidationResult
from app.services.booking_contact_service import BookingContactService
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_revalidation_service import (
    BookingRevalidationConflictError,
    BookingRevalidationService,
    _legacy_quote_legs,
)
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


def _fare() -> CommercialFare:
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
        source_quote_id="Q-HARDEN-REVAL",
        rank=1,
        fare_index=0,
        segments=[_segment()],
        fare=_fare(),
        passenger_mix=[
            PassengerSpec(type=PassengerKind.ADULT, quantity=1)
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


class DelayedProvider:
    provider_name = "fake_sabre"

    def __init__(
        self,
        *,
        delay: float = 0.02,
        error: Exception | None = None,
    ) -> None:
        self.delay = delay
        self.error = error
        self.calls = 0

    async def revalidate(
        self,
        snapshot,
        legs,
        *,
        environment,
    ) -> SabreRevalidationResult:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return SabreRevalidationResult(
            options=[_candidate()],
            transaction_id=f"TX-HARDEN-{self.calls}",
            no_availability=False,
            messages=[],
        )


def _ready_booking(tmp_path, provider: DelayedProvider):
    repository = BookingRepository(tmp_path / "hardening-revalidation.db")
    booking = repository.create_initial(
        source_quote_id="Q-HARDEN-REVAL",
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

    current = repository.get(booking.booking_id)
    assert current is not None
    assert current.status == BookingStatus.READY_FOR_REVIEW
    assert current.revision == 3

    return (
        repository,
        BookingRevalidationService(
            booking_repository=repository,
            provider=provider,
        ),
        current,
    )


@pytest.mark.asyncio
async def test_concurrent_duplicate_revalidation_calls_sabre_once(
    tmp_path,
) -> None:
    provider = DelayedProvider()
    repository, service, booking = _ready_booking(tmp_path, provider)
    request = BookingRevalidationRequest(revision=3)

    first, second = await asyncio.gather(
        service.revalidate(booking.booking_id, request),
        service.revalidate(booking.booking_id, request),
    )

    assert provider.calls == 1
    assert first.revalidation_id == second.revalidation_id
    assert first.booking_revision == second.booking_revision == 4
    assert first.status == second.status == BookingStatus.READY_TO_CREATE_PNR

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.revision == 4
    assert persisted.accepted_offer_revision is not None
    assert persisted.accepted_offer_revision.revision_number == 2


@pytest.mark.asyncio
async def test_completed_request_retry_returns_persisted_result(
    tmp_path,
) -> None:
    provider = DelayedProvider(delay=0)
    _, service, booking = _ready_booking(tmp_path, provider)
    request = BookingRevalidationRequest(revision=3)

    first = await service.revalidate(booking.booking_id, request)
    second = await service.revalidate(booking.booking_id, request)

    assert provider.calls == 1
    assert second.revalidation_id == first.revalidation_id
    assert second.provider_reference == first.provider_reference
    assert second.booking_revision == 4


@pytest.mark.asyncio
async def test_current_revision_performs_intentional_revalidation(
    tmp_path,
) -> None:
    provider = DelayedProvider(delay=0)
    _, service, booking = _ready_booking(tmp_path, provider)

    first = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )
    second = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=first.booking_revision),
    )

    assert provider.calls == 2
    assert second.revalidation_id != first.revalidation_id
    assert second.booking_revision == 5


@pytest.mark.asyncio
async def test_material_mutation_prevents_old_retry_reuse(
    tmp_path,
) -> None:
    provider = DelayedProvider(delay=0)
    repository, service, booking = _ready_booking(tmp_path, provider)

    matched = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )
    assert matched.booking_revision == 4

    BookingContactService(
        booking_repository=repository
    ).update(
        booking.booking_id,
        BookingContactUpdateRequest(
            revision=4,
            name="Test Passenger",
            email="changed@example.com",
            phone_country_code="+54",
            phone_number="1155551234",
            preferred_channel="email",
        ),
    )

    with pytest.raises(BookingRevalidationConflictError, match="Recargá"):
        await service.revalidate(
            booking.booking_id,
            BookingRevalidationRequest(revision=4),
        )

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_timeout_is_persisted_as_agent_action_error(
    tmp_path,
) -> None:
    provider = DelayedProvider(
        delay=0,
        error=TimeoutError("Sabre timeout"),
    )
    _, service, booking = _ready_booking(tmp_path, provider)

    result = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )

    assert result.revalidation_status == RevalidationStatus.ERROR
    assert result.status == BookingStatus.REQUIRES_AGENT_ACTION
    assert result.error_code == "TimeoutError"
    assert result.error_message == "Sabre timeout"


def test_legacy_quote_trip_type_null_can_rebuild_legs() -> None:
    legs = _legacy_quote_legs(
        {
            "origin": "EZE",
            "destination": "MIA",
            "departure_date": "2027-02-10",
            "return_date": "2027-02-20",
            "trip_type": None,
            "adults": 1,
        }
    )

    assert len(legs) == 2
    assert legs[0].origin == "EZE"
    assert legs[0].destination == "MIA"
    assert legs[1].origin == "MIA"
    assert legs[1].destination == "EZE"
