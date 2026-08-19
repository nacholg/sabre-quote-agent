from pathlib import Path


def test_commercial_quote_endpoint_is_exposed():
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert '"/quotes/{quote_id}/commercial"' in source
    assert "response_model=CommercialQuote" in source
    assert "selected_only: bool = False" in source
    assert "build_commercial_quote(" in source


def test_raw_quote_endpoint_is_preserved():
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert '@app.get("/quotes/{quote_id}", response_model=StoredQuoteRecord)' in source
