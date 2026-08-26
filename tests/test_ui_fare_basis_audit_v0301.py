from pathlib import Path


def _source() -> str:
    return Path("app/web/index.html").read_text(encoding="utf-8")


def test_rule_audit_renders_fare_basis_information():
    source = _source()

    assert "function fareBasisAuditHtml(fare)" in source
    assert "fare.fare_components.filter" in source
    assert "component.fare_basis_code" in source
    assert "component.begin_airport" in source
    assert "component.end_airport" in source
    assert "Rule ${component.rule_number}" in source
    assert "Tariff ${component.tariff}" in source
    assert "${fareBasisAuditHtml(f)}" in source


def test_action_selection_preserves_exact_branded_fare():
    source = _source()

    ensure_start = source.index("async function ensureSelection()")
    ensure_end = source.index("function artifactBucket", ensure_start)
    ensure_block = source[ensure_start:ensure_end]

    assert "selectedFareChoices(ranks)" in ensure_block
    assert "JSON.stringify({ranks,fares:fareSelections})" in ensure_block
    assert "currentQuote.selected_fares=saved.selected_fares||[]" in ensure_block
