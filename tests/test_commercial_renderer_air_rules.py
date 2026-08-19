from types import SimpleNamespace

from app.services.commercial_renderer import _fare_lines, _without_prefix


def test_without_prefix_avoids_duplicate_labels():
    assert _without_prefix("No-show: permitido.", "No-show") == "permitido."
    assert _without_prefix("Cambios: permitidos.", "Cambios") == "permitidos."


def test_fare_lines_prefers_commercial_summary():
    fare = SimpleNamespace(
        brand_name="MAIN CABIN",
        brand_code="MAIN",
        cabin="economy",
        currency="USD",
        price_per_passenger=100,
        q1_amount=None,
        q1_currency=None,
    )
    option = SimpleNamespace(is_domestic_argentina=False)

    summary = SimpleNamespace(
        baggage="1 pieza despachada.",
        changes="Cambios permitidos antes y después de la salida; aplica diferencia tarifaria.",
        refunds="Devolución no permitida.",
        no_show="No-show: no permitido según la regla tarifaria.",
        ticketing="Emitir hasta el 2026-08-19.",
    )

    lines = _fare_lines(fare, option, commercial_summary=summary)
    joined = "\n".join(lines)

    assert "Cambios: Cambios:" not in joined
    assert "No-show: No-show:" not in joined
    assert "Cambios: permitidos" in joined
    assert "Devolución: no permitida." in joined
    assert "No-show: no permitido" in joined
