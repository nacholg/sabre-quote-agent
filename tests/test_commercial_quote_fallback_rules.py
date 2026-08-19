from app.models.itinerary import FareOption
from app.services.commercial_quote_builder import _fallback_rule_texts


def test_unknown_refundability_stays_conservative():
    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger="1000.00",
        non_refundable=False,
    )

    changes, refunds = _fallback_rule_texts(fare)

    assert changes == "Cambios: confirmar reglas tarifarias."
    assert refunds == "Devoluciones: confirmar reglas tarifarias."


def test_non_refundable_flag_can_state_refund_not_allowed():
    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger="1000.00",
        non_refundable=True,
    )

    _changes, refunds = _fallback_rule_texts(fare)

    assert refunds == "Devoluciones: no permitidas."
