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
from app.models.quote_request import PassengerKind, PassengerSpec, SearchLeg
from app.sabre.revalidation import SabreRevalidationResult
from app.services.booking_contact_service import BookingContactService
from app.services.booking_create_pnr_builder import (
    BookingCreatePnrPayloadBuilder,
    BookingCreatePnrPayloadError,
    create_booking_payload_fingerprint,
)
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_revalidation_service import BookingRevalidationService
from app.services.booking_repository import BookingRepository


def _segment(**overrides) -> FlightSegment:
    values = dict(
        marketing_carrier="AA",
        operating_carrier="AA",
        flight_number="900",
        departure_airport="EZE",
        arrival_airport="MIA",
        departure_country="AR",
        arrival_country="US",
        departure_at="2027-02-10T21:10:00",
        arrival_at="2027-02-11T05:20:00",
        booking_class="O",
        cabin_code="Y",
    )
    values.update(overrides)
    return FlightSegment(**values)


def _snapshot(*, passenger_mix=None, segments=None, legs=None) -> BookingOfferSnapshot:
    return BookingOfferSnapshot(
        source_quote_id="Q-BUILDER",
        rank=1,
        fare_index=0,
        segments=segments or [_segment()],
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
        passenger_mix=passenger_mix or [
            PassengerSpec(type=PassengerKind.ADULT, quantity=1)
        ],
        legs=legs or [
            SearchLeg(origin="EZE", destination="MIA", departure_date="2027-02-10")
        ],
    )


def _candidate(snapshot: BookingOfferSnapshot) -> ItineraryOption:
    currency = snapshot.fare.currency

    fare = FareOption(
        cabin=snapshot.fare.cabin,
        cabin_codes=["Y"],
        currency=currency,
        price_per_passenger=snapshot.fare.price_per_passenger,
        total_price=snapshot.fare.total_price,
        fare_basis_codes=list(snapshot.fare.fare_basis_codes),
        validating_carrier="AA",
        brand_name=snapshot.fare.brand_name,
        brand_code=snapshot.fare.brand_code,
        branded_components=list(snapshot.fare.branded_components),
        brand_features=list(snapshot.fare.brand_features),
    )
    return ItineraryOption(
        segments=snapshot.segments,
        fare=fare,
        fares_by_currency={currency: fare},
        fare_options_by_currency={currency: [fare]},
    )


class MatchProvider:
    provider_name = "fake_sabre"
    def __init__(self, snapshot):
        self.snapshot = snapshot
    async def revalidate(self, snapshot, legs, *, environment):
        return SabreRevalidationResult(
            options=[_candidate(self.snapshot)],
            transaction_id="TX-BUILDER",
            no_availability=False,
            messages=[],
        )


async def _ready(tmp_path, snapshot, identities):
    repository = BookingRepository(tmp_path / f"{uuid4().hex}.db")
    booking = repository.create_initial(
        source_quote_id="Q-BUILDER",
        selected_rank=1,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=snapshot,
    )
    BookingPassengerService(booking_repository=repository).update(
        booking.booking_id,
        BookingPassengersUpdateRequest(revision=1, passengers=identities),
    )
    BookingContactService(booking_repository=repository).update(
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
    await BookingRevalidationService(
        booking_repository=repository,
        provider=MatchProvider(snapshot),
    ).revalidate(booking.booking_id, BookingRevalidationRequest(revision=3))
    return repository, booking.booking_id


@pytest.mark.asyncio
async def test_builder_direct_payload_and_fingerprint(tmp_path):
    snapshot = _snapshot()
    repository, booking_id = await _ready(
        tmp_path,
        snapshot,
        [{
            "slot_index": 1,
            "given_name": "JOHN",
            "middle_name": "MICHAEL",
            "surname": "TEST",
            "date_of_birth": "1985-04-15",
            "gender": "M",
        }],
    )
    builder = BookingCreatePnrPayloadBuilder(booking_repository=repository)
    payload, fingerprint = builder.build_with_fingerprint(booking_id)

    assert payload["travelers"] == [{
        "givenName": "JOHN MICHAEL",
        "surname": "TEST",
        "birthDate": "1985-04-15",
        "gender": "MALE",
        "passengerCode": "ADT",
    }]
    assert payload["contactInfo"]["phones"] == ["+541155551234"]
    assert payload["flightDetails"]["flights"][0] == {
        "flightNumber": 900,
        "airlineCode": "AA",
        "fromAirportCode": "EZE",
        "toAirportCode": "MIA",
        "departureDate": "2027-02-10",
        "departureTime": "21:10",
        "bookingClass": "O",
        "flightStatusCode": "NN",
        "isMarriageGroup": False,
    }
    assert payload["flightDetails"]["flightPricing"] == []
    assert fingerprint == create_booking_payload_fingerprint(payload)
    assert len(fingerprint) == 64


@pytest.mark.asyncio
async def test_builder_maps_adt_cnn_inf_counts(tmp_path):
    snapshot = _snapshot(passenger_mix=[
        PassengerSpec(type=PassengerKind.ADULT, quantity=1),
        PassengerSpec(type=PassengerKind.CHILD, quantity=1, age=7),
        PassengerSpec(type=PassengerKind.INFANT, quantity=1),
    ])
    repository, booking_id = await _ready(tmp_path, snapshot, [
        {"slot_index": 1, "given_name": "ADULT", "surname": "TEST", "date_of_birth": "1985-04-15", "gender": "M"},
        {"slot_index": 2, "given_name": "CHILD", "surname": "TEST", "date_of_birth": "2020-01-15", "gender": "F"},
        {"slot_index": 3, "given_name": "INFANT", "surname": "TEST", "date_of_birth": "2026-08-15", "gender": "M", "associated_adult_slot_index": 1},
    ])
    payload = BookingCreatePnrPayloadBuilder(
        booking_repository=repository,
        include_flight_pricing=True,
    ).build(booking_id)
    assert [x["passengerCode"] for x in payload["travelers"]] == ["ADT", "CNN", "INF"]
    qualifiers = payload["flightDetails"]["flightPricing"][0]["qualifiers"]
    assert qualifiers["validatingAirlineCode"] == "AA"
    assert qualifiers["brandedFares"] == [{
        "brandCode": "MAIN",
        "flightIndices": [1],
    }]
    assert [x["passengerCode"] for x in qualifiers["passengersPricing"]] == ["ADT", "CNN", "INF"]


@pytest.mark.asyncio
async def test_builder_blocks_connections_without_marriage_metadata(tmp_path):
    snapshot = _snapshot(
        segments=[
            _segment(arrival_airport="DFW", flight_number="100"),
            _segment(departure_airport="DFW", flight_number="200", departure_at="2027-02-11T07:00:00"),
        ],
        legs=[SearchLeg(origin="EZE", destination="MIA", departure_date="2027-02-10")],
    )
    repository, booking_id = await _ready(
        tmp_path,
        snapshot,
        [{"slot_index": 1, "given_name": "TEST", "surname": "PASSENGER", "date_of_birth": "1985-04-15", "gender": "M"}],
    )
    with pytest.raises(BookingCreatePnrPayloadError, match="isMarriageGroup"):
        BookingCreatePnrPayloadBuilder(booking_repository=repository).build(booking_id)


def test_part2_has_no_network_write():
    combined = "\n".join(
        Path(path).read_text(encoding="utf-8").lower()
        for path in [
            "app/services/booking_create_pnr_builder.py",
            "scripts/build_create_booking_payload.py",
        ]
    )
    assert "sabreclient" not in combined
    assert "httpx" not in combined
    assert ".post(" not in combined
    assert "trip/orders/createbooking" not in combined

@pytest.mark.asyncio
async def test_builder_omits_automatic_flight_pricing_by_default_and_allows_explicit_experiment(
    tmp_path,
):
    snapshot = _snapshot()
    repository, booking_id = await _ready(
        tmp_path,
        snapshot,
        [{
            "slot_index": 1,
            "given_name": "TEST",
            "surname": "PASSENGER",
            "date_of_birth": "1985-04-15",
            "gender": "M",
        }],
    )

    default_builder = BookingCreatePnrPayloadBuilder(
        booking_repository=repository
    )
    experimental_builder = BookingCreatePnrPayloadBuilder(
        booking_repository=repository,
        include_flight_pricing=True,
    )

    default_payload, default_fingerprint = (
        default_builder.build_with_fingerprint(booking_id)
    )
    experimental_payload, experimental_fingerprint = (
        experimental_builder.build_with_fingerprint(booking_id)
    )

    assert default_payload["flightDetails"]["flightPricing"] == []

    experimental_pricing = experimental_payload["flightDetails"]["flightPricing"]
    assert len(experimental_pricing) == 1
    qualifiers = experimental_pricing[0]["qualifiers"]
    assert qualifiers["validatingAirlineCode"] == "AA"
    assert qualifiers["brandedFares"] == [{
        "brandCode": "MAIN",
        "flightIndices": [1],
    }]
    assert qualifiers["passengersPricing"] == [{
        "passengerCode": "ADT",
        "forcePassengerCode": False,
        "numberOfpassengers": 1,
    }]

    assert default_fingerprint != experimental_fingerprint
    assert (
        default_fingerprint
        == create_booking_payload_fingerprint(default_payload)
    )
    assert (
        experimental_fingerprint
        == create_booking_payload_fingerprint(experimental_payload)
    )


@pytest.mark.asyncio
async def test_builder_branded_pricing_maps_same_brand_to_all_direct_legs(
    tmp_path,
):
    snapshot = _snapshot(
        segments=[
            _segment(),
            _segment(
                departure_airport="MIA",
                arrival_airport="EZE",
                departure_country="US",
                arrival_country="AR",
                flight_number="901",
                departure_at="2027-02-20T20:00:00",
                arrival_at="2027-02-21T08:00:00",
            ),
        ],
        legs=[
            SearchLeg(
                origin="EZE",
                destination="MIA",
                departure_date="2027-02-10",
            ),
            SearchLeg(
                origin="MIA",
                destination="EZE",
                departure_date="2027-02-20",
            ),
        ],
    )

    repository, booking_id = await _ready(
        tmp_path,
        snapshot,
        [{
            "slot_index": 1,
            "given_name": "TEST",
            "surname": "PASSENGER",
            "date_of_birth": "1985-04-15",
            "gender": "M",
        }],
    )

    payload = BookingCreatePnrPayloadBuilder(
        booking_repository=repository,
        include_flight_pricing=True,
    ).build(booking_id)

    qualifiers = payload["flightDetails"]["flightPricing"][0]["qualifiers"]

    assert qualifiers["brandedFares"] == [{
        "brandCode": "MAIN",
        "flightIndices": [1, 2],
    }]

@pytest.mark.asyncio
async def test_builder_blocks_branded_fare_without_exact_brand_code(tmp_path):
    snapshot = _snapshot()
    snapshot.fare.brand_name = "MAIN CABIN FLEXIBLE"
    snapshot.fare.brand_code = None

    repository, booking_id = await _ready(
        tmp_path,
        snapshot,
        [{
            "slot_index": 1,
            "given_name": "TEST",
            "surname": "PASSENGER",
            "date_of_birth": "1985-04-15",
            "gender": "M",
        }],
    )

    with pytest.raises(
        BookingCreatePnrPayloadError,
        match="BrandID exacto",
    ):
        BookingCreatePnrPayloadBuilder(
            booking_repository=repository,
            include_flight_pricing=True,
        ).build(booking_id)


@pytest.mark.asyncio
async def test_builder_blocks_mixed_brand_components_without_flight_mapping(
    tmp_path,
):
    from app.models.itinerary import BrandedComponent

    snapshot = _snapshot()
    snapshot.fare.brand_code = "MAIN"
    snapshot.fare.branded_components = [
        BrandedComponent(
            begin_airport="EZE",
            end_airport="MIA",
            brand_code="MAIN",
        ),
        BrandedComponent(
            begin_airport="MIA",
            end_airport="EZE",
            brand_code="MAINFL",
        ),
    ]

    repository, booking_id = await _ready(
        tmp_path,
        snapshot,
        [{
            "slot_index": 1,
            "given_name": "TEST",
            "surname": "PASSENGER",
            "date_of_birth": "1985-04-15",
            "gender": "M",
        }],
    )

    with pytest.raises(
        BookingCreatePnrPayloadError,
        match="BrandID",
    ):
        BookingCreatePnrPayloadBuilder(
            booking_repository=repository,
            include_flight_pricing=True,
        ).build(booking_id)

@pytest.mark.asyncio
async def test_builder_carries_mainfl_exactly_into_create_booking(tmp_path):
    snapshot = _snapshot()
    snapshot.fare.brand_name = "MAIN CABIN FLEXIBLE"
    snapshot.fare.brand_code = "MAINFL"
    snapshot.fare.fare_basis_codes = ["SLN7AHM5/L040"]
    snapshot.fare.total_price = Decimal("781.33")
    snapshot.fare.price_per_passenger = Decimal("781.33")

    repository, booking_id = await _ready(
        tmp_path,
        snapshot,
        [{
            "slot_index": 1,
            "given_name": "TEST",
            "surname": "PASSENGER",
            "date_of_birth": "1985-04-15",
            "gender": "M",
        }],
    )

    payload = BookingCreatePnrPayloadBuilder(
        booking_repository=repository,
        include_flight_pricing=True,
    ).build(booking_id)

    pricing = payload["flightDetails"]["flightPricing"]
    assert len(pricing) == 1

    qualifiers = pricing[0]["qualifiers"]

    assert qualifiers["validatingAirlineCode"] == "AA"
    assert qualifiers["currencyPricing"] == "USD"
    assert qualifiers["brandedFares"] == [{
        "brandCode": "MAINFL",
        "flightIndices": [1],
    }]
    assert qualifiers["passengersPricing"] == [{
        "passengerCode": "ADT",
        "forcePassengerCode": False,
        "numberOfpassengers": 1,
    }]

    # Fare basis is identity/audit data. It must NOT be used as forced pricing.
    serialized = str(payload)
    assert "SLN7AHM5/L040" not in serialized


@pytest.mark.asyncio
async def test_builder_uses_selected_fare_currency_for_pricing(tmp_path):
    snapshot = _snapshot()
    snapshot.fare.currency = "EUR"

    repository, booking_id = await _ready(
        tmp_path,
        snapshot,
        [{
            "slot_index": 1,
            "given_name": "TEST",
            "surname": "PASSENGER",
            "date_of_birth": "1985-04-15",
            "gender": "M",
        }],
    )

    payload = BookingCreatePnrPayloadBuilder(
        booking_repository=repository,
        include_flight_pricing=True,
    ).build(booking_id)

    qualifiers = payload["flightDetails"]["flightPricing"][0]["qualifiers"]
    assert qualifiers["currencyPricing"] == "EUR"
