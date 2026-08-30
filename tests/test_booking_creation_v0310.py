from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api import (
    QuoteFareChoice,
    QuoteSearchAPIRequest,
    QuoteSearchAPIResponse,
    RankedOption,
)
from app.models.booking import BookingCreateRequest, BookingStatus
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services.booking_repository import (
    BookingIdempotencyConflictError,
    BookingRepository,
    reset_booking_repository_for_tests,
)
from app.services.booking_service import (
    BookingSelectionError,
    BookingService,
)
from app.services.quote_repository import (
    QuoteRepository,
    QuoteVersionConflictError,
    reset_quote_repository_for_tests,
)


def _request() -> QuoteSearchAPIRequest:
    return QuoteSearchAPIRequest(
        environment="cert",
        origin="EZE",
        destination="MIA",
        departure_date="2026-10-10",
        adults=1,
        children=1,
        child_age=7,
        currency="USD",
    )


def _fare(
    *,
    brand_name: str,
    brand_code: str,
    amount: str,
    fare_basis: str,
) -> FareOption:
    return FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal(amount),
        total_price=Decimal(amount) * 2,
        brand_name=brand_name,
        brand_code=brand_code,
        fare_basis_codes=[fare_basis],
        validating_carrier="AA",
        baggage=["1 pieza despachada por pasajero."],
        non_refundable=False,
    )


def _response() -> QuoteSearchAPIResponse:
    fares = [
        _fare(
            brand_name="MAIN",
            brand_code="M",
            amount="500.00",
            fare_basis="OLN0ATM1",
        ),
        _fare(
            brand_name="FLEX",
            brand_code="F",
            amount="650.00",
            fare_basis="OLN0AFM1",
        ),
    ]
    itinerary = ItineraryOption(
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
        fare=fares[0],
        fares_by_currency={"USD": fares[0]},
        fare_options_by_currency={"USD": fares},
        source_index=0,
    )
    return QuoteSearchAPIResponse(
        environment="CERT",
        effective_currencies=["USD"],
        calls=[],
        result_count=1,
        options=[
            RankedOption(
                rank=1,
                score=Decimal("1"),
                stops=0,
                duration_minutes=510,
                ranking_currency="USD",
                ranking_price=Decimal("1000.00"),
                itinerary=itinerary,
            )
        ],
        client_quote="TEST",
    )


def _selected_quote(tmp_path):
    db = tmp_path / "quotes.db"
    quote_repository = QuoteRepository(db)
    booking_repository = BookingRepository(db)
    quote_id = quote_repository.create(
        request=_request(),
        response=_response(),
    )
    quote_repository.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=1)],
    )
    service = BookingService(
        quote_repository=quote_repository,
        booking_repository=booking_repository,
    )
    return quote_repository, booking_repository, service, quote_id


def test_create_booking_freezes_exact_server_side_product(tmp_path) -> None:
    _, _, service, quote_id = _selected_quote(tmp_path)

    booking = service.create_from_quote(
        quote_id,
        BookingCreateRequest(
            rank=1,
            client_request_id=uuid4(),
        ),
    )

    assert booking.status == BookingStatus.DRAFT
    assert booking.source_quote_id == quote_id
    assert booking.selected_rank == 1
    assert booking.accepted_offer_revision is not None

    snapshot = booking.accepted_offer_revision.snapshot
    assert snapshot.rank == 1
    assert snapshot.fare_index == 1
    assert snapshot.fare.brand_name == "FLEX"
    assert snapshot.fare.fare_basis_codes == ["OLN0AFM1"]
    assert snapshot.segments[0].flight_number == "900"
    assert snapshot.segments[0].booking_class == "O"
    assert [(leg.origin, leg.destination) for leg in snapshot.legs] == [
        ("EZE", "MIA"),
    ]
    assert [(item.type.value, item.quantity, item.age) for item in snapshot.passenger_mix] == [
        ("ADT", 1, None),
        ("CHILD", 1, 7),
    ]


def test_booking_create_is_idempotent_for_same_request(tmp_path) -> None:
    _, _, service, quote_id = _selected_quote(tmp_path)
    request_id = uuid4()
    payload = BookingCreateRequest(
        rank=1,
        client_request_id=request_id,
    )

    first = service.create_from_quote(quote_id, payload)
    second = service.create_from_quote(quote_id, payload)

    assert second.booking_id == first.booking_id
    assert second.accepted_offer_revision_id == first.accepted_offer_revision_id


def test_client_request_id_cannot_be_reused_for_other_quote(tmp_path) -> None:
    db = tmp_path / "quotes.db"
    quote_repository = QuoteRepository(db)
    booking_repository = BookingRepository(db)
    service = BookingService(
        quote_repository=quote_repository,
        booking_repository=booking_repository,
    )

    quote_ids = []
    for _ in range(2):
        quote_id = quote_repository.create(
            request=_request(),
            response=_response(),
        )
        quote_repository.select(
            quote_id,
            [1],
            [QuoteFareChoice(rank=1, fare_index=0)],
        )
        quote_ids.append(quote_id)

    request_id = uuid4()
    service.create_from_quote(
        quote_ids[0],
        BookingCreateRequest(
            rank=1,
            client_request_id=request_id,
        ),
    )

    with pytest.raises(BookingIdempotencyConflictError):
        service.create_from_quote(
            quote_ids[1],
            BookingCreateRequest(
                rank=1,
                client_request_id=request_id,
            ),
        )


def test_booking_requires_exact_persisted_fare(tmp_path) -> None:
    db = tmp_path / "quotes.db"
    quote_repository = QuoteRepository(db)
    booking_repository = BookingRepository(db)
    quote_id = quote_repository.create(
        request=_request(),
        response=_response(),
    )
    quote_repository.select(quote_id, [1], [])

    service = BookingService(
        quote_repository=quote_repository,
        booking_repository=booking_repository,
    )

    with pytest.raises(BookingSelectionError):
        service.create_from_quote(
            quote_id,
            BookingCreateRequest(
                rank=1,
                client_request_id=uuid4(),
            ),
        )


def test_booking_snapshot_survives_quote_selection_change(tmp_path) -> None:
    quote_repository, booking_repository, service, quote_id = _selected_quote(tmp_path)

    booking = service.create_from_quote(
        quote_id,
        BookingCreateRequest(
            rank=1,
            client_request_id=uuid4(),
        ),
    )
    quote_repository.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=0)],
    )

    reloaded = booking_repository.get(booking.booking_id)
    assert reloaded is not None
    assert reloaded.accepted_offer_revision is not None
    assert reloaded.accepted_offer_revision.snapshot.fare.brand_name == "FLEX"
    assert reloaded.accepted_offer_revision.snapshot.fare_index == 1


def test_historical_quote_cannot_start_booking(tmp_path) -> None:
    db = tmp_path / "quotes.db"
    quote_repository = QuoteRepository(db)
    booking_repository = BookingRepository(db)

    original_id = quote_repository.create(
        request=_request(),
        response=_response(),
    )
    quote_repository.select(
        original_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=0)],
    )
    refreshed_id = quote_repository.create(
        request=_request(),
        response=_response(),
        parent_quote_id=original_id,
    )
    quote_repository.link_refresh(original_id, refreshed_id)

    service = BookingService(
        quote_repository=quote_repository,
        booking_repository=booking_repository,
    )

    with pytest.raises(QuoteVersionConflictError):
        service.create_from_quote(
            original_id,
            BookingCreateRequest(
                rank=1,
                client_request_id=uuid4(),
            ),
        )


def test_booking_api_create_and_get(tmp_path, monkeypatch) -> None:
    db = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db))
    reset_quote_repository_for_tests()
    reset_booking_repository_for_tests()

    quote_repository = QuoteRepository(db)
    quote_id = quote_repository.create(
        request=_request(),
        response=_response(),
    )
    quote_repository.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=1)],
    )
    quote_repository.close()

    reset_quote_repository_for_tests()
    reset_booking_repository_for_tests()
    request_id = uuid4()

    with TestClient(app) as client:
        created = client.post(
            f"/quotes/{quote_id}/bookings",
            json={
                "rank": 1,
                "client_request_id": str(request_id),
            },
        )
        assert created.status_code == 201
        payload = created.json()
        booking_id = payload["booking_id"]

        loaded = client.get(f"/bookings/{booking_id}")

    assert loaded.status_code == 200
    assert payload["source_quote_id"] == quote_id
    assert payload["accepted_offer_revision"]["snapshot"]["fare"]["brand_name"] == "FLEX"
    assert UUID(payload["client_request_id"]) == request_id

    reset_quote_repository_for_tests()
    reset_booking_repository_for_tests()


def test_no_create_pnr_route_is_exposed() -> None:
    paths = {
        route.path
        for route in app.routes
        if hasattr(route, "path")
    }
    assert "/bookings/{booking_id}/create-pnr" not in paths
