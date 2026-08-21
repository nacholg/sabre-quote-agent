from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse
from app.services.quote_refresh import refresh_stored_quote
from app.services.quote_repository import (
    QuoteRepository,
    QuoteVersionConflictError,
    reset_quote_repository_for_tests,
)


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


def _version_pair(repo: QuoteRepository) -> tuple[str, str]:
    old_id = _quote(repo)
    new_id = _quote(repo, parent_quote_id=old_id, source="refresh")
    repo.link_refresh(old_id, new_id)
    return old_id, new_id


def test_repository_blocks_mutations_on_historical_quote(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    old_id, new_id = _version_pair(repo)

    with pytest.raises(QuoteVersionConflictError, match="solo lectura"):
        repo.select(old_id, [1])

    with pytest.raises(QuoteVersionConflictError, match="solo lectura"):
        repo.clear_selection(old_id)

    with pytest.raises(QuoteVersionConflictError, match="solo lectura"):
        repo.update_workflow(old_id, notes="No debe cambiar")

    updated = repo.update_workflow(new_id, notes="Última versión editable")
    assert updated.notes == "Última versión editable"


@pytest.mark.asyncio
async def test_refresh_rejects_historical_quote_before_sabre_call(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    old_id, _ = _version_pair(repo)

    with pytest.raises(QuoteVersionConflictError, match="solo lectura"):
        await refresh_stored_quote(repo, old_id)


def test_mutating_endpoints_return_409_for_historical_quote(tmp_path, monkeypatch):
    db_path = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db_path)
    old_id, _ = _version_pair(repo)
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        select = client.post(
            f"/quotes/{old_id}/select",
            json={"ranks": [1]},
        )
        clear = client.delete(f"/quotes/{old_id}/select")
        workflow = client.patch(
            f"/quotes/{old_id}/workflow",
            json={"notes": "No debe cambiar"},
        )
        refresh = client.post(f"/quotes/{old_id}/refresh")

    for response in (select, clear, workflow, refresh):
        assert response.status_code == 409
        assert "solo lectura" in response.json()["detail"]


def test_workspace_disables_mutations_for_historical_versions():
    html = (ROOT / "app" / "web" / "index.html").read_text(encoding="utf-8")

    assert "function applyVersionMutability(" in html
    assert 'data-current-only="true"' in html
    assert "histórica · solo lectura" in html
    assert 'document.querySelectorAll(".rank-check")' in html
    assert "if(currentVersionHistory && !currentVersionHistory.is_latest)return;" in html
