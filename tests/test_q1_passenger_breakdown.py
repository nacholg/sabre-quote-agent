from decimal import Decimal
from types import SimpleNamespace

from app.services.normalizer import _normalize_passenger_prices
from app.services.commercial_renderer import (
    _fare_lines,
    _passenger_price_lines,
)


def _info(ptc, qty, fare, q1_ref):
    return {
        "passengerInfo": {
            "passengerType": ptc,
            "passengerNumber": qty,
            "fareComponents": [],
            "taxes": [{"ref": q1_ref}],
            "passengerTotalFare": {
                "totalFare": fare,
                "totalTaxAmount": 0,
                "currency": "ARS",
            },
        }
    }


def test_q1_is_normalized_for_every_passenger_type():
    passenger_info_list = [
        _info("ADT", 4, 1000, 1),
        _info("C09", 4, 800, 2),
        _info("INF", 1, 100, 3),
    ]
    tax_descs = {
        1: {"id": 1, "code": "Q1", "amount": 100, "currency": "ARS"},
        2: {"id": 2, "code": "Q1", "amount": 80, "currency": "ARS"},
        3: {"id": 3, "code": "Q1", "amount": 20, "currency": "ARS"},
    }

    prices = _normalize_passenger_prices(
        passenger_info_list,
        {"totalPrice": 7300, "currency": "ARS"},
        tax_descs,
    )
    by_ptc = {p.passenger_type: p for p in prices}

    assert by_ptc["ADT"].quantity == 4
    assert by_ptc["ADT"].q1_amount == Decimal("100")
    assert by_ptc["ADT"].q1_total == Decimal("400")

    assert by_ptc["C09"].quantity == 4
    assert by_ptc["C09"].q1_amount == Decimal("80")
    assert by_ptc["C09"].q1_total == Decimal("320")

    assert by_ptc["INF"].quantity == 1
    assert by_ptc["INF"].q1_amount == Decimal("20")
    assert by_ptc["INF"].q1_total == Decimal("20")


def test_q1_group_mode_does_not_double_multiply():
    passenger_info_list = [
        _info("ADT", 2, 2000, 1),
        _info("C09", 2, 1600, 2),
    ]
    tax_descs = {
        1: {"id": 1, "code": "Q1", "amount": 200, "currency": "ARS"},
        2: {"id": 2, "code": "Q1", "amount": 160, "currency": "ARS"},
    }

    prices = _normalize_passenger_prices(
        passenger_info_list,
        {"totalPrice": 3600, "currency": "ARS"},
        tax_descs,
    )
    by_ptc = {p.passenger_type: p for p in prices}

    assert by_ptc["ADT"].q1_amount == Decimal("100")
    assert by_ptc["ADT"].q1_total == Decimal("200")
    assert by_ptc["C09"].q1_amount == Decimal("80")
    assert by_ptc["C09"].q1_total == Decimal("160")


def _pax(ptc, qty, unit, q1, q1_total):
    return SimpleNamespace(
        passenger_type=ptc,
        quantity=qty,
        currency="ARS",
        unit_price=Decimal(unit),
        q1_amount=Decimal(q1),
        q1_total=Decimal(q1_total),
        q1_currency="ARS",
    )


def test_commercial_lines_show_q1_per_ptc_and_grand_total():
    fare = SimpleNamespace(
        brand_name="MAIN CABIN",
        brand_code="MAIN",
        cabin="economy",
        currency="ARS",
        price_per_passenger=Decimal("1000"),
        total_price=Decimal("7200"),
        passenger_prices=[
            _pax("ADT", 4, "1000", "100", "400"),
            _pax("C09", 4, "800", "80", "320"),
        ],
        q1_amount=Decimal("100"),
        q1_currency="ARS",
    )

    joined = "\n".join(_passenger_price_lines(fare))
    assert "Adulto ×4" in joined
    assert "Niño 9 años ×4" in joined
    assert "c/u" in joined
    assert "Q1 total incluido: ARS" in joined

    option = SimpleNamespace(
        is_domestic_argentina=False,
        segments=[],
    )
    summary = SimpleNamespace(
        baggage=None,
        changes=None,
        refunds=None,
        no_show=None,
    )
    rendered = "\n".join(
        _fare_lines(
            fare,
            option,
            commercial_summary=summary,
        )
    )

    assert rendered.count("Q1 total incluido:") == 1
    assert "\nQ1 incluido:" not in rendered
