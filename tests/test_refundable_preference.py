from decimal import Decimal

from app.models.itinerary import BrandFeature, FareOption, ItineraryOption
from app.services.fare_preference_filter import (
    filter_refundable_itineraries,
    is_confirmed_refundable,
)


def fare(name: str, application: str | None, price: str) -> FareOption:
    features = []
    if application:
        features.append(
            BrandFeature(
                application=application,
                commercial_name="REFUND BEFORE DEPARTURE",
            )
        )
    return FareOption(
        cabin="premium economy",
        currency="USD",
        price_per_passenger=Decimal(price),
        total_price=Decimal(price),
        brand_name=name,
        non_refundable=False,
        brand_features=features,
    )


def option(fares: list[FareOption]) -> ItineraryOption:
    return ItineraryOption(
        segments=[],
        fare=fares[0],
        fares_by_currency={"USD": fares[0]},
        fare_options_by_currency={"USD": fares},
    )


def test_confirmed_refundable_requires_explicit_brand_attribute():
    assert is_confirmed_refundable(fare("FLEX", "F", "100")) is True
    assert is_confirmed_refundable(fare("FLEX FEE", "C", "110")) is True
    assert is_confirmed_refundable(fare("NO REFUND", "N", "90")) is False
    assert is_confirmed_refundable(fare("UNKNOWN", None, "95")) is False


def test_filter_removes_nonrefundable_products_but_keeps_itinerary():
    no_refund = fare("COMFORT", "N", "100")
    refundable = fare("FLEX", "C", "150")
    result = filter_refundable_itineraries([option([no_refund, refundable])])
    assert len(result) == 1
    kept = result[0].fare_options_by_currency["USD"]
    assert [item.brand_name for item in kept] == ["FLEX"]
    assert result[0].fare.brand_name == "FLEX"


def test_filter_drops_itinerary_when_no_confirmed_refundable_product_exists():
    result = filter_refundable_itineraries(
        [option([fare("COMFORT", "N", "100"), fare("UNKNOWN", None, "120")])]
    )
    assert result == []
