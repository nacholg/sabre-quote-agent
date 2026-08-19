from pathlib import Path
from types import SimpleNamespace

from app.services.commercial_renderer import _fare_lines


def test_commercial_fare_lines_do_not_render_ticketing():
    fare = SimpleNamespace(
        brand_name="MAIN CABIN", brand_code="MAIN", cabin="economy",
        currency="USD", price_per_passenger=100, q1_amount=None, q1_currency=None,
    )
    option = SimpleNamespace(is_domestic_argentina=False)
    summary = SimpleNamespace(
        baggage="1 pieza despachada.",
        changes="Cambios permitidos.",
        refunds="Devolución no permitida.",
        no_show="No-show: no permitido.",
        ticketing="Emitir hasta el 2026-08-19.",
    )
    joined = "\n".join(_fare_lines(fare, option, commercial_summary=summary))
    assert "Emisión:" not in joined
    assert "Emitir hasta" not in joined


def test_audit_ui_does_not_render_ticketing():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert 'fareRuleCommercialText(f, "ticketing", f.ticketing)' not in html
