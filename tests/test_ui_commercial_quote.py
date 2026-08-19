from pathlib import Path


def _source() -> str:
    return Path("app/web/index.html").read_text(encoding="utf-8")


def test_ui_fetches_canonical_commercial_quote_after_search():
    source = _source()
    assert (
        "currentCommercialQuote=await api("
        "`/quotes/${currentQuoteId}/commercial?selected_only=false`"
        ")" in source
    )
    assert "renderQuote(data.quote,currentCommercialQuote);" in source


def test_ui_fetches_canonical_commercial_quote_when_opening_history():
    source = _source()
    assert (
        "const commercial=await api("
        "`/quotes/${id}/commercial?selected_only=false`"
        ");" in source
    )
    assert "currentCommercialQuote=commercial;" in source
    assert "renderQuote(currentQuote,currentCommercialQuote);" in source


def test_ui_renders_options_and_fares_from_canonical_model():
    source = _source()
    assert "const options=cq?.options||[];" in source
    assert "const flights=(item.segments||[]).map" in source
    assert "const fares=(item.fares||[]).map" in source
    assert "const rules=f.rules||{};" in source
    assert 'commercialRuleLine("Cambios",rules.changes)' in source
    assert 'commercialRuleLine("Devoluciones",rules.refunds)' in source


def test_ui_keeps_technical_quote_for_time_match_and_selection():
    source = _source()
    assert 'const tm=q.time_match||{status:"not_requested"};' in source
    assert "(currentQuote?.selected_ranks||[]).includes(item.rank)" in source


def test_ui_render_quote_no_longer_builds_fares_from_technical_itinerary():
    source = _source()
    start = source.index("function renderQuote(q,commercial=null){")
    end = source.index("function selectedRanks()", start)
    body = source[start:end]

    assert "item.itinerary" not in body
    assert "allCommercialFares(" not in body
