import tomllib
from pathlib import Path

from app.main import app
from app.version import __version__


def test_application_version_matches_project_version():
    pyproject = tomllib.loads(
        Path("pyproject.toml").read_text(encoding="utf-8")
    )

    assert __version__ == "0.31.1"
    assert app.version == __version__
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "app.version.__version__"
    }
