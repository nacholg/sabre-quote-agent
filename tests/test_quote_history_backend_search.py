from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse
from app.services.quote_repository import QuoteRepository, reset_quote_repository_for_tests

ROOT = Path(__file__).resolve().parents[1]


def _request(origin, destination, departure_date, return_date=None):
    return QuoteSearchAPIRequest(
        environment="cert",
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=1,
        currency="USD",
        persist=True,
    )


def _response():
    return QuoteSearchAPIResponse(
        environment="CERT",
        effective_currencies=["USD"],
        calls=[],
        result_count=5,
        options=[],
        client_quote="TEST QUOTE",
    )


def _seed(repo):
    madrid = repo.create(
        request=_request("EZE", "MAD", "2027-01-20", "2027-01-27"),
        response=_response(),
        source="agent",
        agent_text="Cotizar Madrid para Acme",
    )
    repo.update_workflow(
        madrid,
        client_name="Acme Travel",
        client_reference="EXP-7788",
        status="selected",
    )

    miami = repo.create(
        request=_request("EZE", "MIA", "2027-02-10", "2027-02-20"),
        response=_response(),
        source="agent",
        agent_text="Cotizar Miami",
    )
    repo.update_workflow(
        miami,
        client_name="Beta",
        client_reference="REF-MIA",
    )
    return madrid, miami


def test_repository_searches_route_date_client_reference_and_status(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    madrid, miami = _seed(repo)

    assert [q.quote_id for q in repo.list(search="EZE MAD")] == [madrid]
    assert [q.quote_id for q in repo.list(search="2027-01-27")] == [madrid]
    assert [q.quote_id for q in repo.list(search="acme")] == [madrid]
    assert [q.quote_id for q in repo.list(search="exp-7788")] == [madrid]
    assert [q.quote_id for q in repo.list(search="MIA")] == [miami]
    assert [q.quote_id for q in repo.list(search="selected")] == [madrid]


def test_quotes_endpoint_uses_backend_search(tmp_path, monkeypatch):
    db_path = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db_path)
    madrid, _ = _seed(repo)
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        response = client.get(
            "/quotes",
            params={"q": "EZE MAD Acme", "limit": 100},
        )

    assert response.status_code == 200
    assert [row["quote_id"] for row in response.json()] == [madrid]


def test_workspace_uses_debounced_backend_history_search():
    html = (ROOT / "app" / "web" / "index.html").read_text(encoding="utf-8")

    assert 'oninput="scheduleHistorySearch()"' in html
    assert "new URLSearchParams" in html
    assert 'params.set("q",needle)' in html
    assert "búsqueda en servidor" in html
    assert "JSON.stringify(q).toLowerCase().includes(needle)" not in html
