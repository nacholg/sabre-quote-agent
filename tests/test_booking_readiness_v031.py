from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.models.api import (
    BookingContact,
    BookingDraftUpdate,
    BookingPassenger,
    QuoteFareChoice,
    QuoteSearchAPIRequest,
    QuoteSearchAPIResponse,
    RankedOption,
)
from app.models.itinerary import (
    FareOption,
    FlightSegment,
    ItineraryOption,
)
from app.services.booking_readiness import assess_booking_readiness
from app.services.quote_repository import (
    QuoteRepository,
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


def _response(
    *,
    booking_class: str | None = "O",
) -> QuoteSearchAPIResponse:
    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal("500.00"),
        total_price=Decimal("1000.00"),
        brand_name="FLEX",
        brand_code="F",
        fare_basis_codes=["OLN0ATM1"],
        baggage_pieces=1,
        baggage=["1 pieza despachada por pasajero."],
        non_refundable=False,
    )
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
                booking_class=booking_class,
            )
        ],
        fare=fare,
        fares_by_currency={"USD": fare},
        fare_options_by_currency={"USD": [fare]},
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


def _valid_draft() -> BookingDraftUpdate:
    return BookingDraftUpdate(
        passengers=[
            BookingPassenger(
                passenger_type="ADT",
                given_name="JUAN",
                surname="PEREZ",
            ),
            BookingPassenger(
                passenger_type="CHD",
                given_name="MATEO",
                surname="PEREZ",
                date_of_birth="2019-05-01",
            ),
        ],
        contact=BookingContact(
            email="cliente@example.com",
            phone="+54 11 5555 5555",
        ),
        received_from="JUAN PEREZ",
        remarks="Reserva de prueba v0.31",
    )


def _ready_repo(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(
        request=_request(),
        response=_response(),
    )
    repo.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=0)],
    )
    return repo, quote_id


def test_booking_draft_persists_round_trip(tmp_path):
    repo, quote_id = _ready_repo(tmp_path)
    saved = repo.save_booking_draft(
        quote_id,
        _valid_draft(),
    )

    assert saved.quote_id == quote_id
    assert saved.passengers[1].passenger_type == "CHD"

    repo.close()
    reopened = QuoteRepository(tmp_path / "quotes.db")
    loaded = reopened.get_booking_draft(quote_id)

    assert loaded.contact.email == "cliente@example.com"
    assert loaded.received_from == "JUAN PEREZ"
    assert len(loaded.passengers) == 2


def test_readiness_blocks_without_selection_and_draft(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(
        request=_request(),
        response=_response(),
    )

    record = repo.get(quote_id)
    draft = repo.get_booking_draft(quote_id)
    assert record is not None

    readiness = assess_booking_readiness(record, draft)
    codes = {item.code for item in readiness.blockers}

    assert readiness.ready is False
    assert "single_option_required" in codes
    assert "passenger_mix_mismatch" in codes
    assert "contact_missing" in codes
    assert "received_from_missing" in codes


def test_readiness_true_with_exact_fare_and_complete_draft(tmp_path):
    repo, quote_id = _ready_repo(tmp_path)
    repo.save_booking_draft(
        quote_id,
        _valid_draft(),
    )

    record = repo.get(quote_id)
    draft = repo.get_booking_draft(quote_id)
    assert record is not None

    readiness = assess_booking_readiness(record, draft)

    assert readiness.ready is True
    assert readiness.blockers == []
    assert readiness.selected_rank == 1
    assert readiness.selected_fare is not None
    assert readiness.selected_fare.fare.brand_name == "FLEX"
    assert readiness.selected_fare.fare.fare_basis_codes == [
        "OLN0ATM1"
    ]
    assert readiness.expected_passengers == {
        "ADT": 1,
        "CHD": 1,
        "INF": 0,
    }


def test_readiness_detects_passenger_mix_mismatch(tmp_path):
    repo, quote_id = _ready_repo(tmp_path)
    draft = _valid_draft()
    draft.passengers = draft.passengers[:1]
    repo.save_booking_draft(quote_id, draft)

    record = repo.get(quote_id)
    stored = repo.get_booking_draft(quote_id)
    assert record is not None

    readiness = assess_booking_readiness(record, stored)
    codes = {item.code for item in readiness.blockers}

    assert readiness.ready is False
    assert "passenger_mix_mismatch" in codes


def test_readiness_requires_dob_for_child(tmp_path):
    repo, quote_id = _ready_repo(tmp_path)
    draft = _valid_draft()
    draft.passengers[1].date_of_birth = None
    repo.save_booking_draft(quote_id, draft)

    record = repo.get(quote_id)
    stored = repo.get_booking_draft(quote_id)
    assert record is not None

    readiness = assess_booking_readiness(record, stored)
    codes = {item.code for item in readiness.blockers}

    assert "passenger_dob_missing" in codes


def test_readiness_requires_booking_class(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(
        request=_request(),
        response=_response(booking_class=None),
    )
    repo.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=0)],
    )
    repo.save_booking_draft(
        quote_id,
        _valid_draft(),
    )

    record = repo.get(quote_id)
    draft = repo.get_booking_draft(quote_id)
    assert record is not None

    readiness = assess_booking_readiness(record, draft)
    codes = {item.code for item in readiness.blockers}

    assert readiness.ready is False
    assert "booking_class_missing" in codes


def test_booking_readiness_endpoints(tmp_path, monkeypatch):
    db = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db)
    quote_id = repo.create(
        request=_request(),
        response=_response(),
    )
    repo.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=0)],
    )
    repo.close()
    reset_quote_repository_for_tests()

    payload = _valid_draft().model_dump(mode="json")

    with TestClient(app) as client:
        saved = client.put(
            f"/quotes/{quote_id}/booking-draft",
            json=payload,
        )
        readiness = client.get(
            f"/quotes/{quote_id}/booking-readiness"
        )
        cleared = client.delete(
            f"/quotes/{quote_id}/booking-draft"
        )

    assert saved.status_code == 200
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert cleared.status_code == 200
    assert cleared.json()["passengers"] == []
