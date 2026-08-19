from decimal import Decimal
from types import SimpleNamespace

from app.services.commercial_renderer import (
    _passenger_label,
    _passenger_price_lines,
)


def pax(ptc, qty, unit, total):
    return SimpleNamespace(
        passenger_type=ptc,
        quantity=qty,
        age=None,
        currency="USD",
        unit_price=Decimal(unit),
        total_price=Decimal(total),
    )


def test_child_age_code_label():
    assert _passenger_label(
        pax("C10", 1, "100", "100")
    ) == "Niño 10 años"


def test_mixed_passenger_prices_render_separately():
    fare = SimpleNamespace(
        currency="USD",
        price_per_passenger=Decimal("837.33"),
        total_price=Decimal("1506.66"),
        passenger_prices=[
            pax("ADT", 1, "837.33", "837.33"),
            pax("C10", 1, "669.33", "669.33"),
        ],
    )

    lines = _passenger_price_lines(fare)

    assert "Adulto ×1: USD 837.33" in lines
    assert "Niño 10 años ×1: USD 669.33" in lines
    assert "Total: USD 1,506.66" in lines


def test_all_adult_single_ptc_keeps_legacy_compact_price():
    fare = SimpleNamespace(
        currency="USD",
        price_per_passenger=Decimal("1143.33"),
        total_price=Decimal("2286.66"),
        passenger_prices=[
            pax("ADT", 2, "1143.33", "2286.66"),
        ],
    )

    assert _passenger_price_lines(fare) == [
        "USD 1,143.33"
    ]
