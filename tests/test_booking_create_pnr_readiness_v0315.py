from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.booking import (
    BookingContactUpdateRequest,
    BookingOfferSnapshot,
    BookingPassengersUpdateRequest,
    BookingRevalidationRequest,
)
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.models.quote_request import (
    PassengerKind,
    PassengerSpec,
    SearchLeg,
)
from app.sabre.revalidation import SabreRevalidationResult
from app.services.booking_contact_service import BookingContactService
from app.services.booking_create_pnr_readiness_service import (
    BookingCreatePnrReadinessService,
    sabre_create_booking_passenger_code,
)
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_revalidation_service import BookingRevalidationService
from app.services.booking_repository import BookingRepository


def _segment(
    *,
    origin: str = "EZE",
    destination: str = "MIA",
    flight_number: str = "982",
    departure_at: str = "2027-02-10T10:10:00",
    arrival_at: str = "2027-02-10T17:20:00",
    booking_class: str = "O",
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


def _fare(total: str = "450.73") -> CommercialFare:
    return CommercialFare(
        cabin="economy",
        currency="USD",
        brand_name="MAIN",
        brand_code="MAIN",
        price_per_passenger=Decimal(total),
        total_price=Decimal(total),
        fare_basis_codes=["OLN0ATM1"],
        validating_carrier="AA",
    )


def _snapshot(
    *,
    passenger_mix: list[PassengerSpec] | None = None,
    segments: list[FlightSegment] | None = None,
    legs: list[SearchLeg] | None = None,
) -> BookingOfferSnapshot:
    return BookingOfferSnapshot(
        source_quote_id="Q-HARDEN",
        rank=1,
        fare_index=0,
        segments=segments or [_segment()],
        fare=_fare(),
        passenger_mix=passenger_mix or [
            PassengerSpec(
                type=PassengerKind.ADULT,
                quantity=1,
            )
        ],
        legs=legs if legs is not None else [
            SearchLeg(
                origin="EZE",
                destination="MIA",
                departure_date="2027-02-10",
                departure_time="10:10:00",
            )
        ],
    )


def _candidate(snapshot: BookingOfferSnapshot) -> ItineraryOption:
    fare = FareOption(
        cabin=snapshot.fare.cabin,
        cabin_codes=["Y"],
        currency=snapshot.fare.currency,
        price_per_passenger=snapshot.fare.price_per_passenger,
        total_price=snapshot.fare.total_price,
        fare_basis_codes=list(snapshot.fare.fare_basis_codes),
        validating_carrier=snapshot.fare.validating_carrier,
        brand_name=snapshot.fare.brand_name,
        brand_code=snapshot.fare.brand_code,
    )
    return ItineraryOption(
        segments=snapshot.segments,
        fare=fare,
        fares_by_currency={"USD": fare},
        fare_options_by_currency={"USD": [fare]},
    )


class FakeProvider:
    provider_name = "fake_sabre"

    def __init__(self, option: ItineraryOption) -> None:
        self.option = option

    async def revalidate(
        self,
        snapshot,
        legs,
        *,
        environment,
    ) -> SabreRevalidationResult:
        return SabreRevalidationResult(
            options=[self.option],
            transaction_id="TX-HARDEN",
            no_availability=False,
            messages=[],
        )


async def _ready_booking(
    tmp_path,
    *,
    snapshot: BookingOfferSnapshot | None = None,
):
    repository = BookingRepository(tmp_path / "hardening.db")
    frozen = snapshot or _snapshot()

    booking = repository.create_initial(
        source_quote_id="Q-HARDEN",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=frozen,
    )

    passengers = []
    slot_index = 1
    for spec in frozen.passenger_mix:
        for _ in range(spec.quantity):
            if spec.type == PassengerKind.ADULT:
                dob = "1985-04-15"
            elif spec.type == PassengerKind.CHILD:
                dob = f"{2027 - int(spec.age or 6)}-01-15"
            else:
                dob = "2026-08-15"

            passengers.append(
                {
                    "slot_index": slot_index,
                    "given_name": f"TEST{slot_index}",
                    "surname": "PASSENGER",
                    "date_of_birth": dob,
                    "gender": "M",
                    "associated_adult_slot_index": (
                        1
                        if spec.type == PassengerKind.INFANT
                        else None
                    ),
                }
            )
            slot_index += 1

    BookingPassengerService(
        booking_repository=repository
    ).update(
        booking.booking_id,
        BookingPassengersUpdateRequest(
            revision=1,
            passengers=passengers,
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
    assert current.revision == 3

    result = await BookingRevalidationService(
        booking_repository=repository,
        provider=FakeProvider(_candidate(frozen)),
    ).revalidate(
        booking.booking_id,
        BookingRevalidationRequest(revision=3),
    )
    assert result.revalidation_status.value == "matched"

    return repository, booking.booking_id


@pytest.mark.asyncio
async def test_matched_booking_passes_create_pnr_readiness_without_quote(
    tmp_path,
) -> None:
    repository, booking_id = await _ready_booking(tmp_path)

    service = BookingCreatePnrReadinessService(
        booking_repository=repository
    )
    result = service.get(booking_id)

    assert result.ready is True
    assert result.reasons == []
    assert result.status.value == "ready_to_create_pnr"
    assert result.revalidation_status.value == "matched"
    assert result.accepted_offer_revision_id is not None
    assert result.revalidation_id is not None
    assert result.passenger_count == 1
    assert result.segment_count == 1
    assert result.sabre_passenger_codes == ["ADT"]

    # No QuoteRepository dependency: v0.32 can consume the Booking alone.
    assert not hasattr(service, "quote_repository")


@pytest.mark.asyncio
async def test_material_contact_edit_invalidates_create_pnr_readiness(
    tmp_path,
) -> None:
    repository, booking_id = await _ready_booking(tmp_path)

    booking = repository.get(booking_id)
    assert booking is not None

    BookingContactService(
        booking_repository=repository
    ).update(
        booking_id,
        BookingContactUpdateRequest(
            revision=booking.revision,
            name="Test Passenger",
            email="changed@example.com",
            phone_country_code="+54",
            phone_number="1155551234",
            preferred_channel="email",
        ),
    )

    result = BookingCreatePnrReadinessService(
        booking_repository=repository
    ).get(booking_id)

    assert result.ready is False
    assert "booking_status_not_ready" in result.reasons
    assert "booking_revalidation_not_matched" in result.reasons
    assert "revalidation_stale" in result.reasons


@pytest.mark.asyncio
async def test_connection_warns_when_marriage_metadata_is_not_captured(
    tmp_path,
) -> None:
    snapshot = _snapshot(
        segments=[
            _segment(
                destination="DFW",
                flight_number="100",
                arrival_at="2027-02-10T16:00:00",
            ),
            _segment(
                origin="DFW",
                destination="MIA",
                flight_number="200",
                departure_at="2027-02-10T18:00:00",
                arrival_at="2027-02-10T21:30:00",
            ),
        ],
    )
    repository, booking_id = await _ready_booking(
        tmp_path,
        snapshot=snapshot,
    )

    result = BookingCreatePnrReadinessService(
        booking_repository=repository
    ).get(booking_id)

    assert result.ready is True
    assert (
        "marriage_group_metadata_not_captured"
        in result.warnings
    )


def test_create_booking_passenger_type_mapping() -> None:
    assert (
        sabre_create_booking_passenger_code(PassengerKind.ADULT)
        == "ADT"
    )
    assert (
        sabre_create_booking_passenger_code(PassengerKind.CHILD)
        == "CNN"
    )
    assert (
        sabre_create_booking_passenger_code(PassengerKind.INFANT)
        == "INF"
    )


def test_v0315_readiness_gate_contains_no_create_booking_call() -> None:
    paths = [
        Path("app/services/booking_create_pnr_readiness_service.py"),
    ]
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in paths
    ).lower()

    assert "/trip/orders/createbooking" not in combined
    assert "createpassengernamerecordrq" not in combined
