from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.models.api import AgentInterpretation, QuoteSearchAPIRequest, QuoteSearchAPIResponse
from app.services.quote_repository import QuoteRepository, reset_quote_repository_for_tests


def _request() -> QuoteSearchAPIRequest:
    return QuoteSearchAPIRequest(
        environment="cert",
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        return_date="2026-09-30",
        adults=1,
        currency="USD",
        persist=True,
    )


def _response() -> QuoteSearchAPIResponse:
    return QuoteSearchAPIResponse(
        environment="CERT",
        effective_currencies=["USD"],
        calls=[],
        result_count=5,
        options=[],
        client_quote="TEST QUOTE",
    )


def test_repository_create_get_and_list(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    request = _request()
    response = _response()

    quote_id = repo.create(request=request, response=response)
    assert quote_id.startswith("Q-")

    record = repo.get(quote_id)
    assert record is not None
    assert record.quote_id == quote_id
    assert record.source == "structured"
    assert record.search_request["origin"] == "EZE"
    assert record.quote_response["client_quote"] == "TEST QUOTE"
    assert record.quote_response["quote_id"] == quote_id

    summaries = repo.list()
    assert len(summaries) == 1
    assert summaries[0].quote_id == quote_id
    assert summaries[0].origin == "EZE"
    assert summaries[0].destination == "MIA"
    assert summaries[0].result_count == 5


def test_repository_can_attach_agent_context(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    request = _request()
    response = _response()
    quote_id = repo.create(request=request, response=response)

    interpretation = AgentInterpretation(
        confidence=0.98,
        search_request=request,
    )
    repo.attach_agent_context(
        quote_id,
        text="Cotizame EZE-MIA",
        interpretation=interpretation,
    )

    record = repo.get(quote_id)
    assert record is not None
    assert record.source == "agent"
    assert record.agent_text == "Cotizame EZE-MIA"
    assert record.interpretation["confidence"] == 0.98


def test_quotes_history_endpoints(tmp_path, monkeypatch):
    db_path = tmp_path / "api_quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db_path)
    quote_id = repo.create(request=_request(), response=_response())

    # Force singleton to notice the same env path on next call.
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        list_response = client.get("/quotes")
        get_response = client.get(f"/quotes/{quote_id}")
        missing_response = client.get("/quotes/Q-NOT-FOUND")

    assert list_response.status_code == 200
    assert list_response.json()[0]["quote_id"] == quote_id
    assert get_response.status_code == 200
    assert get_response.json()["quote_id"] == quote_id
    assert missing_response.status_code == 404
