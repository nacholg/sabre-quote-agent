from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.models.booking import BookingOfferSnapshot
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import BrandedComponent, FlightSegment
from app.models.quote_request import PassengerKind, PassengerSpec, SearchLeg
from app.sabre.revalidation import build_revalidate_request


def _snapshot(*, branded: bool) -> BookingOfferSnapshot:
    fare = CommercialFare(
        cabin="economy",
        currency="USD",
        brand_name=("MAIN CABIN FLEXIBLE" if branded else None),
        brand_code=("MAINFL" if branded else None),
        price_per_passenger=Decimal("1548.93"),
        total_price=Decimal("1548.93"),
        fare_basis_codes=["NLN0DTM5/L040"],
        validating_carrier="AA",
        branded_components=(
            [
                BrandedComponent(
                    component_ref=4,
                    begin_airport="EZE",
                    end_airport="JFK",
                    fare_basis_code="NLN0DTM5/L040",
                    governing_carrier="AA",
                    brand_code="MAINFL",
                    brand_name="MAIN CABIN FLEXIBLE",
                    program_code="AAWHLH",
                ),
                BrandedComponent(
                    component_ref=5,
                    begin_airport="JFK",
                    end_airport="EZE",
                    fare_basis_code="NLN0DTM5/L040",
                    governing_carrier="AA",
                    brand_code="MAINFL",
                    brand_name="MAIN CABIN FLEXIBLE",
                    program_code="AAWHLH",
                ),
            ]
            if branded
            else []
        ),
    )

    return BookingOfferSnapshot(
        source_quote_id="Q-TEST",
        rank=1,
        fare_index=1,
        segments=[
            FlightSegment(
                marketing_carrier="AA",
                operating_carrier="AA",
                flight_number="954",
                departure_airport="EZE",
                arrival_airport="JFK",
                departure_at=datetime(2026, 1, 20, 21, 10),
                arrival_at=datetime(2026, 1, 21, 6, 0),
                booking_class="N",
            ),
            FlightSegment(
                marketing_carrier="AA",
                operating_carrier="AA",
                flight_number="953",
                departure_airport="JFK",
                arrival_airport="EZE",
                departure_at=datetime(2026, 1, 28, 22, 0),
                arrival_at=datetime(2026, 1, 29, 10, 45),
                booking_class="N",
            ),
        ],
        fare=fare,
        passenger_mix=[
            PassengerSpec(type=PassengerKind.ADULT, quantity=1)
        ],
        legs=[
            SearchLeg(
                origin="EZE",
                destination="JFK",
                departure_date=date(2026, 1, 20),
            ),
            SearchLeg(
                origin="JFK",
                destination="EZE",
                departure_date=date(2026, 1, 28),
            ),
        ],
    )


def test_branded_revalidation_requests_multiple_brands_without_fixed_legs() -> None:
    snapshot = _snapshot(branded=True)
    rq = build_revalidate_request(
        snapshot,
        snapshot.legs,
        "RY3A",
    )["OTA_AirLowFareSearchRQ"]

    price_info = rq["TravelerInfoSummary"]["PriceRequestInformation"]
    indicators = price_info["TPA_Extensions"]["BrandedFareIndicators"]

    assert indicators["SingleBrandedFare"] is True
    assert indicators["MultipleBrandedFares"] is True
    assert indicators["ReturnBrandAncillaries"] is True
    assert indicators["UpsellLimit"] == 3
    assert indicators["BrandFilters"] == {
        "Brand": [
            {
                "Code": "MAINFL",
                "PreferLevel": "Preferred",
            }
        ]
    }
    assert "NonBrandedFares" not in indicators["BrandFilters"]
    assert "InterlineBrands" not in rq["TravelPreferences"]

    for od in rq["OriginDestinationInformation"]:
        assert "Fixed" not in od
        for flight in od["TPA_Extensions"]["Flight"]:
            assert "Fare" not in flight


def test_unbranded_revalidation_does_not_request_multiple_brands() -> None:
    snapshot = _snapshot(branded=False)
    rq = build_revalidate_request(
        snapshot,
        snapshot.legs,
        "RY3A",
    )["OTA_AirLowFareSearchRQ"]

    price_info = rq["TravelerInfoSummary"]["PriceRequestInformation"]
    assert "TPA_Extensions" not in price_info
