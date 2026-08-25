from pathlib import Path


def _source() -> str:
    return Path("app/web/index.html").read_text(encoding="utf-8")


def test_ui_sends_exact_fare_index_with_selected_rank():
    source = _source()

    assert "function selectedFareChoices(ranks)" in source
    assert "fare_index:Number(fareIndex)" in source
    assert "JSON.stringify({ranks,fares:fareSelections})" in source


def test_ui_restores_persisted_fare_choice_from_quote_record():
    source = _source()

    assert "function hydratePersistedFareChoices()" in source
    assert "currentQuote?.selected_fares||[]" in source
    assert "selectedFareIndexByRank.set(rank,fareIndex)" in source
    assert "currentQuote.selected_fares=rec.selected_fares||[]" in source
    assert "selectedFareQuoteId=null" in source


def test_ui_keeps_server_canonical_selection_response():
    source = _source()

    assert "const saved=await api(`/quotes/${currentQuoteId}/select`" in source
    assert "currentQuote.selected_ranks=saved.selected_ranks||ranks" in source
    assert "currentQuote.selected_fares=saved.selected_fares||[]" in source


def test_ui_clear_selection_clears_persisted_fare_metadata():
    source = _source()

    assert "currentQuote.selected_ranks=[]" in source
    assert "currentQuote.selected_fares=[]" in source
