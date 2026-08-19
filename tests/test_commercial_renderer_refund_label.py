from types import SimpleNamespace

from app.services.commercial_renderer import _fare_lines


def test_plural_refund_fallback_label_is_preserved():
    fare = SimpleNamespace(
        brand_name="FLEX",
        brand_code="FLEX",
        cabin="economy",
        currency="USD",
        price_per_passenger=1000,
        passenger_prices=[],
        q1_amount=None,
        q1_currency=None,
        rules=SimpleNamespace(
            baggage="1 pieza despachada.",
            changes="Cambios: confirmar reglas tarifarias.",
            refunds="Devoluciones: confirmar reglas tarifarias.",
            no_show=None,
        ),
    )
    option = SimpleNamespace(
        is_domestic_argentina=False,
    )

    joined = "\n".join(_fare_lines(fare, option))

    assert "Devoluciones: confirmar reglas tarifarias." in joined
    assert "Devolución: confirmar reglas tarifarias." not in joined
