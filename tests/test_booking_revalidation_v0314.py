from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.bookings as bookings_api
from app.main import app
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
from app.models.quote_request import (
    PassengerKind,
    PassengerSpec,
    SearchLeg,
)
from app.sabre.revalidation import (
    SabreRevalidationResult,
    build_revalidate_request,
)
from app.services.booking_contact_service import BookingContactService
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_revalidation_service import (
    BookingRevalidationConflictError,
    BookingRevalidationService,
)
from app.services.booking_repository import BookingRepository


def _segment(
    *,
    flight_number: str = "982",
    booking_class: str = "O",
    departure_at: str = "2027-02-10T10:10:00",
    arrival_at: str = "2027-02-10T17:20:00",
    origin: str = "EZE",
    destination: str = "MIA",
) -> FlightSegment:
    return FlightSegment(
        marketing_carrier="AA",
        operating_carrier="AA",
        flight_number=flight_number,
        departure_airport=origin,
        arrival_airport=destination,
        departure_country="AR",
        arrival_country="US",
        departure_at=departure_at,
        arrival_at=arrival_at,
        booking_class=booking_class,
        cabin_code="Y",
    )


def _fare(
    *,
    total: str = "450.73",
    fare_basis: str = "OLN0ATM1",
    brand_code: str | None = "MAIN",
) -> CommercialFare:
    return CommercialFare(
        cabin="economy",
        currency="USD",
        brand_name="MAIN",
        brand_code=brand_code,
        price_per_passenger=Decimal(total),
        total_price=Decimal(total),
        fare_basis_codes=[fare_basis],
        validating_carrier="AA",
    )


def _snapshot() -> BookingOfferSnapshot:
    return BookingOfferSnapshot(
        source_quote_id="Q-REVAL",
        rank=1,
        fare_index=0,
        segments=[_segment()],
        fare=_fare(),
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


def _candidate(
    *,
    total: str = "450.73",
    fare_basis: str = "OLN0ATM1",
    brand_code: str | None = "MAIN",
    flight_number: str = "982",
    booking_class: str = "O",
    departure_at: str = "2027-02-10T10:10:00",
) -> ItineraryOption:
    fare = FareOption(
        cabin="economy",
        cabin_codes=["Y"],
        currency="USD",
        price_per_passenger=Decimal(total),
        total_price=Decimal(total),
        fare_basis_codes=[fare_basis],
        validating_carrier="AA",
        brand_name="MAIN",
        brand_code=brand_code,
    )
    return ItineraryOption(
        segments=[
            _segment(
                flight_number=flight_number,
                booking_class=booking_class,
                departure_at=departure_at,
            )
        ],
        fare=fare,
        fares_by_currency={"USD": fare},
        fare_options_by_currency={"USD": [fare]},
    )


class FakeProvider:
    provider_name = "fake_sabre"

    def __init__(
        self,
        *,
        options: list[ItineraryOption] | None = None,
        no_availability: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.options = options or []
        self.no_availability = no_availability
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
        if self.error is not None:
            raise self.error
        return SabreRevalidationResult(
            options=self.options,
            transaction_id="TX-REVAL-1",
            no_availability=self.no_availability,
            messages=[],
        )


def _ready_booking(tmp_path, provider: FakeProvider):
    repository = BookingRepository(tmp_path / "revalidation.db")
    booking = repository.create_initial(
        source_quote_id="Q-REVAL",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=_snapshot(),
    )

    passengers = BookingPassengerService(
        booking_repository=repository
    ).update(
        booking.booking_id,
        BookingPassengersUpdateRequest(
            revision=1,
            passengers=[
                {
                    "slot_index": 1,
                    "given_name": "Test",
                    "surname": "Passenger",
                    "date_of_birth": "1985-04-15",
                    "gender": "M",
                }
            ],
        ),
    )
    assert passengers.complete is True

    contact = BookingContactService(
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
    assert contact.complete is True

    ready = repository.get(booking.booking_id)
    assert ready is not None
    assert ready.status == BookingStatus.READY_FOR_REVIEW
    assert ready.revision == 3

    service = BookingRevalidationService(
        booking_repository=repository,
        provider=provider,
    )
    return repository, service, ready


def test_revalidate_request_pins_exact_flight_and_booking_class() -> None:
    snapshot = _snapshot()
    payload = build_revalidate_request(
        snapshot,
        snapshot.legs,
        "RY3A",
    )["OTA_AirLowFareSearchRQ"]

    assert payload["Version"] == "5"
    assert (
        payload["TPA_Extensions"]["IntelliSellTransaction"]
        ["RequestType"]["Name"]
        == "REVALIDATE"
    )
    assert (
        payload["TPA_Extensions"]["IntelliSellTransaction"]
        ["ServiceTag"]["Name"]
        == "REVALIDATE"
    )

    od = payload["OriginDestinationInformation"][0]
    flight = od["TPA_Extensions"]["Flight"][0]
    assert flight["Airline"] == {
        "Marketing": "AA",
        "Operating": "AA",
    }
    assert flight["Number"] == 982
    assert flight["ClassOfService"] == "O"
    assert flight["OriginLocation"]["LocationCode"] == "EZE"
    assert flight["DestinationLocation"]["LocationCode"] == "MIA"
    assert flight["DepartureDateTime"] == "2027-02-10T10:10:00"
    assert payload["TravelerInfoSummary"]["SeatsRequested"] == [1]


def test_revalidate_request_groups_connection_inside_one_leg() -> None:
    snapshot = _snapshot()
    snapshot.segments = [
        _segment(
            flight_number="100",
            origin="EZE",
            destination="DFW",
            arrival_at="2027-02-10T16:00:00",
        ),
        _segment(
            flight_number="200",
            origin="DFW",
            destination="MIA",
            departure_at="2027-02-10T18:00:00",
            arrival_at="2027-02-10T21:30:00",
        ),
    ]

    payload = build_revalidate_request(
        snapshot,
        snapshot.legs,
        "RY3A",
    )["OTA_AirLowFareSearchRQ"]

    ods = payload["OriginDestinationInformation"]
    assert len(ods) == 1
    assert len(ods[0]["TPA_Extensions"]["Flight"]) == 2
    assert ods[0]["OriginLocation"]["LocationCode"] == "EZE"
    assert ods[0]["DestinationLocation"]["LocationCode"] == "MIA"


@pytest.mark.asyncio
async def test_matched_revalidation_promotes_ready_to_create_pnr(
    tmp_path,
) -> None:
    provider = FakeProvider(options=[_candidate()])
    repository, service, booking = _ready_booking(tmp_path, provider)

    result = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )

    assert result.revalidation_status == RevalidationStatus.MATCHED
    assert result.status == BookingStatus.READY_TO_CREATE_PNR
    assert result.provider_reference == "TX-REVAL-1"
    assert result.candidate_offer_revision_id is not None
    assert result.source_offer_revision_id is not None

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.revision == 4
    assert persisted.status == BookingStatus.READY_TO_CREATE_PNR
    assert persisted.revalidation_status == RevalidationStatus.MATCHED
    assert (
        persisted.accepted_offer_revision_id
        == result.candidate_offer_revision_id
    )
    assert persisted.accepted_offer_revision is not None
    assert persisted.accepted_offer_revision.revision_number == 2
    assert persisted.accepted_offer_revision.source.value == "revalidation"


@pytest.mark.asyncio
async def test_price_change_requires_agent_action(tmp_path) -> None:
    provider = FakeProvider(options=[_candidate(total="499.99")])
    repository, service, booking = _ready_booking(tmp_path, provider)

    result = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )

    assert result.revalidation_status == RevalidationStatus.PRICE_CHANGED
    assert result.status == BookingStatus.REQUIRES_AGENT_ACTION
    assert result.diff["changes"][0]["field"] == "total_price"

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert result.candidate_offer_revision_id is not None
    assert (
        persisted.accepted_offer_revision_id
        == result.source_offer_revision_id
    )


@pytest.mark.asyncio
async def test_fare_change_requires_agent_action(tmp_path) -> None:
    provider = FakeProvider(
        options=[_candidate(fare_basis="OLN9ATM1")]
    )
    _, service, booking = _ready_booking(tmp_path, provider)

    result = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )

    assert result.revalidation_status == RevalidationStatus.FARE_CHANGED
    assert result.status == BookingStatus.REQUIRES_AGENT_ACTION
    assert result.diff["changes"][0]["field"] == "fare_basis_codes"


@pytest.mark.asyncio
async def test_itinerary_change_requires_agent_action(tmp_path) -> None:
    provider = FakeProvider(
        options=[_candidate(booking_class="Q")]
    )
    _, service, booking = _ready_booking(tmp_path, provider)

    result = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )

    assert (
        result.revalidation_status
        == RevalidationStatus.ITINERARY_CHANGED
    )
    assert result.status == BookingStatus.REQUIRES_AGENT_ACTION
    assert result.diff["changes"][0]["field"] == "itinerary"


@pytest.mark.asyncio
async def test_no_availability_is_persisted(tmp_path) -> None:
    provider = FakeProvider(no_availability=True)
    _, service, booking = _ready_booking(tmp_path, provider)

    result = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )

    assert result.revalidation_status == RevalidationStatus.UNAVAILABLE
    assert result.status == BookingStatus.REQUIRES_AGENT_ACTION
    assert result.candidate_offer_revision_id is None


@pytest.mark.asyncio
async def test_provider_error_is_persisted(tmp_path) -> None:
    provider = FakeProvider(error=RuntimeError("provider unavailable"))
    _, service, booking = _ready_booking(tmp_path, provider)

    result = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )

    assert result.revalidation_status == RevalidationStatus.ERROR
    assert result.status == BookingStatus.REQUIRES_AGENT_ACTION
    assert result.error_code == "RuntimeError"
    assert result.error_message == "provider unavailable"


@pytest.mark.asyncio
async def test_revision_conflict_blocks_provider_call(tmp_path) -> None:
    provider = FakeProvider(options=[_candidate()])
    _, service, booking = _ready_booking(tmp_path, provider)

    with pytest.raises(
        BookingRevalidationConflictError,
        match="Recargá",
    ):
        await service.revalidate(
            booking.booking_id,
            BookingRevalidationRequest(revision=2),
        )

    assert provider.calls == 0


@pytest.mark.asyncio
async def test_get_returns_latest_revalidation(tmp_path) -> None:
    provider = FakeProvider(options=[_candidate()])
    _, service, booking = _ready_booking(tmp_path, provider)

    saved = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )
    loaded = service.get(booking.booking_id)

    assert loaded.revalidation_id == saved.revalidation_id
    assert loaded.revalidation_status == RevalidationStatus.MATCHED
    assert loaded.provider_reference == "TX-REVAL-1"


@pytest.mark.asyncio
async def test_contact_mutation_after_match_marks_result_stale(
    tmp_path,
) -> None:
    provider = FakeProvider(options=[_candidate()])
    repository, service, booking = _ready_booking(tmp_path, provider)

    matched = await service.revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )
    assert matched.booking_revision == 4

    contact_service = BookingContactService(
        booking_repository=repository
    )
    contact_service.update(
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

    persisted = repository.get(booking.booking_id)
    assert persisted is not None
    assert persisted.status == BookingStatus.REVALIDATION_REQUIRED
    assert persisted.revalidation_status == RevalidationStatus.STALE

    latest = service.get(booking.booking_id)
    assert latest.stale_at is not None


@pytest.mark.asyncio
async def test_revalidation_api_get_and_post(tmp_path, monkeypatch) -> None:
    provider = FakeProvider(options=[_candidate()])
    _, service, booking = _ready_booking(tmp_path, provider)

    monkeypatch.setattr(
        bookings_api,
        "get_booking_revalidation_service",
        lambda: service,
    )

    with TestClient(app) as client:
        before = client.get(
            f"/bookings/{booking.booking_id}/revalidation"
        )
        assert before.status_code == 200
        assert before.json()["revalidation_id"] is None

        saved = client.post(
            f"/bookings/{booking.booking_id}/revalidation",
            json={"revision": 3},
        )

    assert saved.status_code == 200
    payload = saved.json()
    assert payload["status"] == "ready_to_create_pnr"
    assert payload["revalidation_status"] == "matched"
    assert payload["provider_reference"] == "TX-REVAL-1"


def test_v0314_contains_no_create_pnr_call() -> None:
    paths = [
        Path("app/sabre/revalidation.py"),
        Path("app/services/booking_revalidation_service.py"),
        Path("app/api/bookings.py"),
    ]
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in paths
    ).lower()

    assert "/trip/orders/createbooking" not in combined
    assert "createpassengernamerecordrq" not in combined
