from pathlib import Path


def test_run_quote_structure_is_not_corrupted():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "await\nasync function saveWorkflow" not in html
    assert html.count("async function runQuote(") == 1
    assert html.count("async function saveWorkflow(") == 1
    assert html.count("async function refreshQuote(") == 1


def test_run_quote_keeps_expected_flow():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "if(data.quote){" in html
    assert "renderQuote(data.quote);" in html
    assert "await loadHistory();" in html
