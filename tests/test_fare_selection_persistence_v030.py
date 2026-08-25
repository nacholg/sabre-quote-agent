from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api import (
    QuoteFareChoice,
    QuoteSearchAPIRequest,
    QuoteSearchAPIResponse,
    QuoteSelectionRequest,
    RankedOption,
)
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services.commercial_quote_builder import build_commercial_quote
from app.services.commercial_renderer import render_stored_quote
from app.services.quote_repository import (
    QuoteRepository,
    reset_quote_repository_for_tests,
)


def _fare(name: str, code: str, price: str) -> FareOption:
    return FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal(price),
        total_price=Decimal(price),
        brand_name=name,
        brand_code=code,
        baggage_pieces=1,
        baggage=["1 pieza despachada por pasajero."],
        non_refundable=(name == "BASIC"),
    )


def _request() -> QuoteSearchAPIRequest:
    return QuoteSearchAPIRequest(
        environment="cert",
        origin="EZE",
        destination="MIA",
        departure_date="2026-10-10",
        adults=1,
        currency="USD",
    )


def _response() -> QuoteSearchAPIResponse:
    basic = _fare("BASIC", "B", "500.00")
    flex = _fare("FLEX", "F", "700.00")

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
            )
        ],
        fare=basic,
        fares_by_currency={"USD": basic},
        fare_options_by_currency={"USD": [basic, flex]},
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
                ranking_price=Decimal("500.00"),
                itinerary=itinerary,
            )
        ],
        client_quote="TEST",
    )


def test_selection_request_validates_fare_rank_membership():
    payload = QuoteSelectionRequest(
        ranks=[1],
        fares=[QuoteFareChoice(rank=1, fare_index=0)],
    )
    assert payload.ranks == [1]
    assert payload.fares[0].fare_index == 0

    with pytest.raises(ValueError, match="deben pertenecer"):
        QuoteSelectionRequest(
            ranks=[1],
            fares=[QuoteFareChoice(rank=2, fare_index=0)],
        )


def test_repository_persists_exact_fare_snapshot(tmp_path):
    db = tmp_path / "quotes.db"
    repo = QuoteRepository(db)
    quote_id = repo.create(request=_request(), response=_response())

    result = repo.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=1)],
    )
    assert result.selected_fares[0].rank == 1
    assert result.selected_fares[0].fare_index == 1
    assert result.selected_fares[0].fare.brand_name == "FLEX"

    repo.close()

    reopened = QuoteRepository(db)
    record = reopened.get(quote_id)
    assert record is not None
    assert len(record.selected_fares) == 1
    assert record.selected_fares[0].fare.brand_name == "FLEX"
    assert record.selected_fares[0].fare.total_price == Decimal("700.00")


def test_selected_commercial_quote_contains_only_exact_fare(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())
    repo.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=1)],
    )
    record = repo.get(quote_id)
    assert record is not None

    quote = build_commercial_quote(record, selected_only=True)
    assert len(quote.options) == 1
    assert len(quote.options[0].fares) == 1
    assert quote.options[0].fares[0].brand_name == "FLEX"


def test_renderer_uses_exact_persisted_fare(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())
    repo.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=1)],
    )
    record = repo.get(quote_id)
    assert record is not None

    rendered = render_stored_quote(record, "whatsapp")
    assert "FLEX" in rendered.content
    assert "USD 700.00" in rendered.content
    assert "BASIC" not in rendered.content
    assert "USD 500.00" not in rendered.content


def test_clear_selection_removes_exact_fare(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())
    repo.select(
        quote_id,
        [1],
        [QuoteFareChoice(rank=1, fare_index=1)],
    )

    cleared = repo.clear_selection(quote_id)
    assert cleared.selected_ranks == []
    assert cleared.selected_fares == []

    record = repo.get(quote_id)
    assert record is not None
    assert record.selected_fares == []


def test_select_endpoint_returns_canonical_exact_fare(tmp_path, monkeypatch):
    db = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db)
    quote_id = repo.create(request=_request(), response=_response())
    repo.close()
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        response = client.post(
            f"/quotes/{quote_id}/select",
            json={
                "ranks": [1],
                "fares": [{"rank": 1, "fare_index": 1}],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_ranks"] == [1]
    assert payload["selected_fares"][0]["fare_index"] == 1
    assert payload["selected_fares"][0]["fare"]["brand_name"] == "FLEX"


def test_rank_only_selection_remains_backward_compatible(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())

    result = repo.select(quote_id, [1])
    assert result.selected_ranks == [1]
    assert result.selected_fares == []

    record = repo.get(quote_id)
    assert record is not None
    quote = build_commercial_quote(record, selected_only=True)
    assert len(quote.options[0].fares) >= 1


def test_stored_quote_record_exposes_selected_fares_field():
    from app.models.api import StoredQuoteRecord, StoredQuoteSummary

    assert "selected_fares" in StoredQuoteRecord.model_fields
    assert "selected_fares" not in StoredQuoteSummary.model_fields
