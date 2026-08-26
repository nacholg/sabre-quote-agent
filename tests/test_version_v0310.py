import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.version import __version__


def test_runtime_version_is_v0311() -> None:
    assert __version__ == "0.31.1"
    assert app.version == __version__


def test_health_uses_runtime_version() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["version"] == __version__


def test_pyproject_uses_app_version_as_dynamic_source() -> None:
    data = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )

    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "app.version.__version__"
    }
