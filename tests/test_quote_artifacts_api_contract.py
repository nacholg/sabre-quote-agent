from pathlib import Path


def test_artifact_models_exist():
    src = Path("app/models/api.py").read_text(encoding="utf-8")
    assert "class QuoteArtifactCreate(BaseModel):" in src
    assert "class QuoteArtifactRecord(BaseModel):" in src


def test_artifact_endpoints_exist():
    src = Path("app/main.py").read_text(encoding="utf-8")
    assert '"/quotes/{quote_id}/artifacts"' in src
    assert '"/quotes/{quote_id}/artifacts/{artifact_id}"' in src
    assert "create_quote_artifact" in src
    assert "list_quote_artifacts" in src
    assert "delete_quote_artifact" in src
    assert "clear_quote_artifacts" in src
