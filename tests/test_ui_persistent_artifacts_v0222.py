from pathlib import Path


def source():
    return Path("app/web/index.html").read_text(encoding="utf-8")


def test_workspace_loads_and_persists_artifacts():
    html = source()
    assert "async function persistArtifact(" in html
    assert "async function loadArtifacts(" in html
    assert "await loadArtifacts(currentQuoteId);" in html
    assert 'await persistArtifact({type:"whatsapp"' in html
    assert 'await persistArtifact({type:"email"' in html


def test_workspace_can_delete_artifacts():
    html = source()
    assert "async function deleteArtifact(" in html
    assert "async function clearPersistedArtifacts(" in html
    assert "Limpiar salidas" in html
    assert "clearSessionArtifacts" not in html


def test_rules_and_reprice_are_persisted():
    html = source()
    assert 'type:"rules"' in html
    assert 'type:"reprice"' in html
    assert html.count("await persistArtifact({") >= 4
