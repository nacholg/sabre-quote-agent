import re
from pathlib import Path


def test_application_version_matches_project_version():
    main = Path("app/main.py").read_text(encoding="utf-8")
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    project_version = re.search(
        r'^version = "([^"]+)"$',
        pyproject,
        re.MULTILINE,
    ).group(1)

    assert f'version="{project_version}"' in main
    assert f'"version": "{project_version}"' in main
    assert project_version == "0.21.1"
