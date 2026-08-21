from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import (
    SabreEnvironmentMismatchError,
    get_settings,
    runtime_environment_status,
)
from app.main import app


ROOT = Path(__file__).resolve().parents[1]


def configure_minimal_sabre(monkeypatch, *, sabre_env: str) -> None:
    monkeypatch.setenv("SABRE_ENV", sabre_env)
    monkeypatch.setenv("SABRE_CLIENT_ID", "test-client")
    monkeypatch.setenv("SABRE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("SABRE_PCC", "TEST")
    monkeypatch.setenv("SABRE_TOKEN_TYPE", "client_credentials")
    get_settings.cache_clear()


def test_process_runtime_rejects_requested_environment_mismatch(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    configure_minimal_sabre(monkeypatch, sabre_env="CERT")

    settings = get_settings("cert")
    assert settings.sabre_env.upper() == "CERT"

    get_settings.cache_clear()
    with pytest.raises(
        SabreEnvironmentMismatchError,
        match="configurado para CERT",
    ):
        get_settings("prod")


def test_runtime_status_reports_locked_process_environment(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    configure_minimal_sabre(monkeypatch, sabre_env="CERT")

    status = runtime_environment_status()

    assert status["locked"] is True
    assert status["environment"] == "cert"
    assert status["available_environments"] == ["cert"]
    assert status["read_only"] is True


def test_quotes_search_returns_409_before_any_sabre_call_on_mismatch(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    configure_minimal_sabre(monkeypatch, sabre_env="CERT")

    with TestClient(app) as client:
        response = client.post(
            "/quotes/search",
            json={
                "environment": "prod",
                "origin": "EZE",
                "destination": "MIA",
                "departure_date": "2026-09-19",
                "adults": 1,
                "persist": False,
            },
        )

    assert response.status_code == 409
    assert "configurado para CERT" in response.json()["detail"]


def test_workspace_locks_environment_selector_from_runtime():
    html = (ROOT / "app" / "web" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 'id="runtimeBadge"' in html
    assert "async function loadRuntimeEnvironment()" in html
    assert "envSelect.disabled=true" in html
    assert 'await api("/runtime")' in html
    assert "loadRuntimeEnvironment();" in html
