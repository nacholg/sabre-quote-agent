from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse, RankedOption, StoredQuoteRecord
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services.commercial_renderer import render_stored_quote
from app.services.quote_repository import QuoteRepository, reset_quote_repository_for_tests


def _option(rank: int, flight: str, price: str) -> RankedOption:
    fare = FareOption(
        cabin="economy",
        currency="ARS",
        price_per_passenger=Decimal(price),
        total_price=Decimal(price),
        brand_code="EF",
        brand_name="FLEX",
        baggage_pieces=1,
        baggage=["1 pieza despachada por pasajero."],
        non_refundable=False,
    )
    itinerary = ItineraryOption(
        segments=[
            FlightSegment(
                marketing_carrier="AR",
                operating_carrier="AR",
                flight_number=flight,
                departure_airport="COR",
                arrival_airport="AEP",
                departure_country="AR",
                arrival_country="AR",
                departure_at="2026-09-19T13:15:00-03:00",
                arrival_at="2026-09-19T14:35:00-03:00",
            ),
            FlightSegment(
                marketing_carrier="AR",
                operating_carrier="AR",
                flight_number="1546",
                departure_airport="AEP",
                arrival_airport="COR",
                departure_country="AR",
                arrival_country="AR",
                departure_at="2026-09-30T18:45:00-03:00",
                arrival_at="2026-09-30T20:15:00-03:00",
            ),
        ],
        fare=fare,
        fares_by_currency={"ARS": fare},
        fare_options_by_currency={"ARS": [fare]},
        source_index=rank - 1,
    )
    return RankedOption(
        rank=rank,
        score=Decimal(str(rank)),
        stops=0,
        duration_minutes=170,
        ranking_currency="ARS",
        ranking_price=Decimal(price),
        itinerary=itinerary,
    )


def _request() -> QuoteSearchAPIRequest:
    return QuoteSearchAPIRequest(
        environment="cert",
        origin="COR",
        destination="AEP",
        departure_date="2026-09-19",
        return_date="2026-09-30",
        adults=1,
        direct=True,
        currency="ARS",
        carriers=["AR"],
    )


def _response() -> QuoteSearchAPIResponse:
    return QuoteSearchAPIResponse(
        environment="CERT",
        effective_currencies=["ARS"],
        calls=[],
        result_count=2,
        options=[
            _option(1, "1529", "124079.70"),
            _option(2, "1531", "130000.00"),
        ],
        client_quote="ORIGINAL",
    )


def test_selection_is_persisted(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())

    selected = repo.select(quote_id, [2, 1])
    assert selected.status == "selected"
    assert selected.selected_ranks == [1, 2]

    record = repo.get(quote_id)
    assert record is not None
    assert record.status == "selected"
    assert record.selected_ranks == [1, 2]

    summary = repo.list()[0]
    assert summary.selected_ranks == [1, 2]


def test_invalid_rank_is_rejected(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())
    with pytest.raises(ValueError, match="Ranks inexistentes"):
        repo.select(quote_id, [3])


def test_clear_selection(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())
    repo.select(quote_id, [1])
    cleared = repo.clear_selection(quote_id)
    assert cleared.status == "active"
    assert cleared.selected_ranks == []


def test_whatsapp_renderer_uses_only_selected_options(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())
    repo.select(quote_id, [2])
    record = repo.get(quote_id)
    assert record is not None

    rendered = render_stored_quote(record, "whatsapp")
    assert "AR 1531" in rendered.content
    assert "AR 1529" not in rendered.content
    assert "FLEX — ARS 130,000.00" in rendered.content
    assert "Referencia:" in rendered.content


def test_email_renderer_returns_html(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())
    repo.select(quote_id, [1])
    record = repo.get(quote_id)
    assert record is not None

    rendered = render_stored_quote(record, "email")
    assert rendered.content_type.startswith("text/html")
    assert "<html>" in rendered.content
    assert "AR 1529" in rendered.content


def test_render_requires_selection(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(request=_request(), response=_response())
    record = repo.get(quote_id)
    assert record is not None
    with pytest.raises(ValueError, match="no tiene opciones seleccionadas"):
        render_stored_quote(record, "whatsapp")


def test_selection_and_render_endpoints(tmp_path, monkeypatch):
    db_path = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()
    repo = QuoteRepository(db_path)
    quote_id = repo.create(request=_request(), response=_response())
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        selection = client.post(
            f"/quotes/{quote_id}/select",
            json={"ranks": [1]},
        )
        render = client.get(
            f"/quotes/{quote_id}/render",
            params={"format": "whatsapp"},
        )
        clear = client.delete(f"/quotes/{quote_id}/select")

    assert selection.status_code == 200
    assert selection.json()["selected_ranks"] == [1]
    assert render.status_code == 200
    assert "AR 1529" in render.json()["content"]
    assert clear.status_code == 200
    assert clear.json()["selected_ranks"] == []


def test_direct_whatsapp_endpoint_returns_plain_text(tmp_path, monkeypatch):
    db_path = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db_path)
    quote_id = repo.create(request=_request(), response=_response())
    repo.select(quote_id, [1])
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        response = client.get(f"/quotes/{quote_id}/whatsapp")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "AR 1529" in response.text
    assert "\\n" not in response.text


def test_direct_email_endpoint_returns_html(tmp_path, monkeypatch):
    db_path = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db_path)
    quote_id = repo.create(request=_request(), response=_response())
    repo.select(quote_id, [1])
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        response = client.get(f"/quotes/{quote_id}/email")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<html>" in response.text
    assert "AR 1529" in response.text
