from decimal import Decimal

from app.models.itinerary import FareOption
from app.services.quote_renderer import _select_commercial_fares


def _fare(cabin, brand, price):
    return FareOption(
        cabin=cabin,
        currency="USD",
        price_per_passenger=Decimal(str(price)),
        brand_name=brand,
    )


def test_business_request_keeps_distinct_business_brands():
    fares = [
        _fare("business", "BUSINESS LIGHT", 3000),
        _fare("business", "BUSINESS COMFORT", 3400),
        _fare("business", "BUSINESS FLEX", 3900),
    ]

    selected = _select_commercial_fares(
        fares,
        requested_cabins={"business"},
    )

    assert [fare.brand_name for fare in selected] == [
        "BUSINESS LIGHT",
        "BUSINESS COMFORT",
        "BUSINESS FLEX",
    ]


def test_business_companion_stays_compact_when_not_requested():
    fares = [
        _fare("business", "BUSINESS LIGHT", 3000),
        _fare("business", "BUSINESS COMFORT", 3400),
        _fare("business", "BUSINESS FLEX", 3900),
    ]

    selected = _select_commercial_fares(
        fares,
        requested_cabins={"economy"},
    )

    assert [fare.brand_name for fare in selected] == [
        "BUSINESS LIGHT",
    ]


def test_duplicate_business_brand_keeps_cheapest_price():
    fares = [
        _fare("business", "BUSINESS COMFORT", 3500),
        _fare("business", "BUSINESS COMFORT", 3400),
        _fare("business", "BUSINESS FLEX", 3900),
    ]

    selected = _select_commercial_fares(
        fares,
        requested_cabins={"business"},
    )

    assert [
        (fare.brand_name, fare.price_per_passenger)
        for fare in selected
    ] == [
        ("BUSINESS COMFORT", Decimal("3400")),
        ("BUSINESS FLEX", Decimal("3900")),
    ]


def test_unbranded_business_request_stays_single_fare():
    fares = [
        _fare("business", None, 3000),
        _fare("business", None, 3200),
    ]

    selected = _select_commercial_fares(
        fares,
        requested_cabins={"business"},
    )

    assert len(selected) == 1
    assert selected[0].price_per_passenger == Decimal("3000")


def test_first_requested_is_not_dropped():
    fares = [
        _fare("first", "FIRST", 6000),
        _fare("first", "FIRST FLEX", 7200),
    ]

    selected = _select_commercial_fares(
        fares,
        requested_cabins={"first"},
    )

    assert [fare.brand_name for fare in selected] == [
        "FIRST",
        "FIRST FLEX",
    ]
