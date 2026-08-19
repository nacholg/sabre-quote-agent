from types import SimpleNamespace

from app.services.commercial_renderer import _fare_lines


def test_legacy_commercial_summary_call_remains_supported():
    fare = SimpleNamespace(
        brand_name='MAIN CABIN', brand_code='MAIN', cabin='economy',
        currency='USD', price_per_passenger=100, passenger_prices=[],
        q1_amount=None, q1_currency=None,
    )
    option = SimpleNamespace(is_domestic_argentina=False)
    summary = SimpleNamespace(
        baggage='1 pieza despachada.', changes='Cambios permitidos.',
        refunds='Devolución no permitida.',
        no_show='No-show: no permitido.', ticketing='Emitir hasta mañana.',
    )
    lines = _fare_lines(fare, option, commercial_summary=summary)
    joined = '\n'.join(lines)
    assert 'Equipaje: 1 pieza despachada.' in joined
    assert 'Cambios: permitidos.' in joined
    assert 'Devolución: no permitida.' in joined
    assert 'No-show: no permitido.' in joined
    assert 'Emitir' not in joined
