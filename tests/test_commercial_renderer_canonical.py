from pathlib import Path


def test_commercial_renderer_uses_canonical_builder():
    source = Path('app/services/commercial_renderer.py').read_text(encoding='utf-8')
    assert 'from app.services.commercial_quote_builder import build_commercial_quote' in source
    assert 'quote = build_commercial_quote(record)' in source


def test_commercial_renderer_no_longer_runs_air_rules_directly():
    source = Path('app/services/commercial_renderer.py').read_text(encoding='utf-8')
    assert 'audit_stored_quote_live' not in source
    assert 'def _commercial_rule_map(' not in source
    assert 'def _commercial_fares(' not in source
    assert 'def _fare_key(' not in source


def test_render_contract_is_preserved():
    source = Path('app/services/commercial_renderer.py').read_text(encoding='utf-8')
    assert 'def render_whatsapp(record: StoredQuoteRecord) -> str:' in source
    assert 'def render_email_html(record: StoredQuoteRecord) -> str:' in source
    assert 'def render_stored_quote(record: StoredQuoteRecord, format: str)' in source
