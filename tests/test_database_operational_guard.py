import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.services.quote_service as quote_service_module
from app.main import app
from app.models.api import QuoteSearchAPIRequest
from app.services.quote_repository import (
    QuoteRepository,
    QuoteRepositoryUnavailableError,
)


def _request(*, persist=True):
    return QuoteSearchAPIRequest(
        environment="cert",
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        adults=1,
        persist=persist,
    )


def test_repository_ping_works_with_sqlite(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    repo.ping()


def test_persisted_search_checks_database_before_sabre(monkeypatch):
    events = []

    class BrokenRepository:
        def ping(self):
            events.append("database")
            raise QuoteRepositoryUnavailableError("DB unavailable")

    class ForbiddenSabreClient:
        def __init__(self, *args, **kwargs):
            events.append("sabre")
            raise AssertionError("Sabre must not be called")

    monkeypatch.setattr(
        quote_service_module,
        "get_settings",
        lambda _: SimpleNamespace(sabre_env="CERT"),
    )
    monkeypatch.setattr(
        quote_service_module,
        "get_quote_repository",
        lambda: BrokenRepository(),
    )
    monkeypatch.setattr(
        quote_service_module,
        "SabreClient",
        ForbiddenSabreClient,
    )

    with pytest.raises(QuoteRepositoryUnavailableError):
        asyncio.run(
            quote_service_module.search_quote(
                _request(persist=True)
            )
        )

    assert events == ["database"]


def test_database_unavailable_is_http_503(monkeypatch):
    def unavailable():
        raise QuoteRepositoryUnavailableError(
            "Base de datos no disponible."
        )

    monkeypatch.setattr(
        main_module,
        "get_quote_repository",
        unavailable,
    )

    response = TestClient(app).get("/quotes")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Base de datos no disponible."
    }


def test_health_reports_database_without_secrets(monkeypatch):
    class Repository:
        dialect_name = "postgresql"

        def ping(self):
            return None

    monkeypatch.setattr(
        main_module,
        "get_quote_repository",
        lambda: Repository(),
    )

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == {
        "status": "ok",
        "dialect": "postgresql",
    }
    assert "url" not in str(payload).lower()
    assert "password" not in str(payload).lower()


def test_health_is_degraded_when_database_is_unavailable(monkeypatch):
    def unavailable():
        raise QuoteRepositoryUnavailableError(
            "Base de datos no disponible."
        )

    monkeypatch.setattr(
        main_module,
        "get_quote_repository",
        unavailable,
    )

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == {
        "status": "unavailable",
        "dialect": None,
    }


def test_workspace_shows_safe_history_database_error():
    from pathlib import Path

    source = Path("app/web/index.html").read_text(
        encoding="utf-8"
    )
    assert "Historial no disponible" in source
    assert "<strong>Historial no disponible.</strong>" in source
