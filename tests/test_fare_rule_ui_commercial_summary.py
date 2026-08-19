
from pathlib import Path


def test_fare_rule_ui_has_commercial_summary_helper():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "function fareRuleCommercialText(" in html
    assert "commercial_summary" in html


def test_fare_rule_ui_uses_commercial_summary():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "fareRuleCommercialText(" in html
    assert '"baggage"' in html
    assert '"changes"' in html
    assert '"refunds"' in html
    assert '"ticketing"' not in html
