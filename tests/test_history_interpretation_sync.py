from pathlib import Path


def _source() -> str:
    return Path("app/web/index.html").read_text(
        encoding="utf-8"
    )


def test_history_open_refreshes_interpretation_panel():
    source = _source()

    assert "function renderStoredInterpretation(rec){" in source
    assert "renderStoredInterpretation(rec);" in source


def test_stored_interpretation_is_preferred_when_available():
    source = _source()

    start = source.index("function renderStoredInterpretation(rec){")
    end = source.index("function passengerPriceLabel", start)
    body = source[start:end]

    assert "const stored=rec?.interpretation;" in body
    assert "if(stored?.search_request)" in body
    assert "renderInterpretation(stored);" in body


def test_history_interpretation_falls_back_to_stored_search_request():
    source = _source()

    start = source.index("function renderStoredInterpretation(rec){")
    end = source.index("function passengerPriceLabel", start)
    body = source[start:end]

    assert "const request=rec?.search_request;" in body
    assert "search_request:request" in body
    assert "assumptions:[]" in body
    assert "warnings:[]" in body


def test_open_quote_updates_interpretation_before_rendering_results():
    source = _source()

    start = source.index("async function openQuote(id){")
    end = source.index("loadRuntimeEnvironment();", start)
    body = source[start:end]

    assert body.index("renderStoredInterpretation(rec);") < body.index(
        "renderQuote(currentQuote,currentCommercialQuote);"
    )
