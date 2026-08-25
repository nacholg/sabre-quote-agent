from pathlib import Path

HTML = Path("app/web/index.html").read_text(encoding="utf-8")


def test_v029c_sidebar_defaults_to_four_recent_quotes():
    assert "const historyCompactLimit = 4;" in HTML
    assert 'id="historyToggleButton"' in HTML
    assert "Ver todo el historial" in HTML
    assert "function applyCompactHistoryView()" in HTML
    assert "history-compact-hidden" in HTML


def test_v029c_search_temporarily_expands_history_matches():
    assert 'document.getElementById("historySearch")' in HTML
    assert "historyExpanded=false;" in HTML
    assert "window.setTimeout(applyCompactHistoryView,0);" in HTML


def test_v029c_interpretation_uses_designed_card_grid():
    assert 'id="patagonik-v029c-polish"' in HTML
    assert "#interpretation .interpret{" in HTML
    assert "grid-template-columns:repeat(8,minmax(0,1fr));" in HTML
    assert "#interpretation .chip{" in HTML
    assert "min-height:112px;" in HTML


def test_v029c_results_fit_desktop_and_preserve_price_column():
    assert ".results-table-header," in HTML
    assert ".result-row-grid{" in HTML
    assert "min-width:0;" in HTML
    assert "#options .option-card{" in HTML
    assert ".option-details{" in HTML
    assert ".result-price{" in HTML
    assert "white-space:nowrap;" in HTML


def test_v029c_results_only_scroll_horizontally_on_narrower_screens():
    assert "@media(max-width:1220px)" in HTML
    assert "min-width:1110px;" in HTML
