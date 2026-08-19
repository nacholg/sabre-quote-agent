from pathlib import Path


def test_ui_rule_renderer_uses_commercial_summary_fields():
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    expected = [
        'fareRuleCommercialText(f, "baggage", f.baggage)',
        'fareRuleCommercialText(f, "changes", f.changes)',
        'fareRuleCommercialText(f, "refunds", f.refunds)',
        'fareRuleCommercialText(f, "no_show")',
    ]

    for token in expected:
        assert token in html


def test_ui_has_no_broken_no_show_variable():
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    assert 'fareRuleCommercialText(fare, "no_show")' not in html
    assert 'escapeHtml(fareRuleCommercialText(f, "no_show"))' not in html


def test_ui_rule_renderer_hides_ticketing():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert 'fareRuleCommercialText(f, "ticketing", f.ticketing)' not in html
    assert "Emisión:" not in html
