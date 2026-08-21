from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse
from app.services.quote_repository import QuoteRepository, reset_quote_repository_for_tests


ROOT = Path(__file__).resolve().parents[1]


def _quote(
    repo: QuoteRepository,
    *,
    parent_quote_id: str | None = None,
    source: str = "structured",
) -> str:
    request = QuoteSearchAPIRequest(
        environment="cert",
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        return_date="2026-09-30",
        adults=1,
        currency="USD",
        persist=False,
    )
    response = QuoteSearchAPIResponse(
        environment="CERT",
        effective_currencies=["USD"],
        calls=[],
        result_count=0,
        options=[],
        client_quote="TEST",
    )
    return repo.create(
        request=request,
        response=response,
        source=source,
        parent_quote_id=parent_quote_id,
    )


def _chain(repo: QuoteRepository) -> tuple[str, str, str]:
    q1 = _quote(repo)
    q2 = _quote(repo, parent_quote_id=q1, source="refresh")
    repo.link_refresh(q1, q2)
    q3 = _quote(repo, parent_quote_id=q2, source="refresh")
    repo.link_refresh(q2, q3)
    return q1, q2, q3


def test_repository_returns_complete_version_lineage(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    q1, q2, q3 = _chain(repo)

    history = repo.version_history(q2)

    assert history.root_quote_id == q1
    assert history.latest_quote_id == q3
    assert history.current_version == 2
    assert history.total_versions == 3
    assert history.is_latest is False

    assert [item.quote_id for item in history.versions] == [q1, q2, q3]
    assert [item.version for item in history.versions] == [1, 2, 3]
    assert [item.is_current for item in history.versions] == [False, True, False]
    assert [item.is_latest for item in history.versions] == [False, False, True]
    assert history.versions[0].status == "superseded"
    assert history.versions[1].status == "superseded"
    assert history.versions[2].status == "active"


def test_single_quote_is_version_one_and_latest(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _quote(repo)

    history = repo.version_history(quote_id)

    assert history.root_quote_id == quote_id
    assert history.latest_quote_id == quote_id
    assert history.current_version == 1
    assert history.total_versions == 1
    assert history.is_latest is True
    assert history.versions[0].is_latest is True


def test_versions_endpoint(tmp_path, monkeypatch):
    db_path = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db_path)
    q1, q2, q3 = _chain(repo)
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        response = client.get(f"/quotes/{q2}/versions")

    assert response.status_code == 200
    data = response.json()
    assert data["root_quote_id"] == q1
    assert data["latest_quote_id"] == q3
    assert data["current_version"] == 2
    assert data["total_versions"] == 3


def test_workspace_has_version_navigation():
    html = (ROOT / "app" / "web" / "index.html").read_text(encoding="utf-8")

    assert 'id="versionBar"' in html
    assert "function renderQuoteVersions(" in html
    assert "async function loadQuoteVersions(" in html
    assert "/versions`" in html
    assert "Versión ${current} de ${total}" in html
