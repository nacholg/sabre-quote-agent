from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.models.commercial_quote import CommercialFare
from app.models.itinerary import BrandFeature, BrandedComponent


def test_commercial_fare_preserves_branded_identity() -> None:
    fare = CommercialFare(
        cabin="economy",
        currency="USD",
        brand_name="MAIN PLUS",
        brand_code="MAINPL",
        price_per_passenger=Decimal("1100.00"),
        total_price=Decimal("1100.00"),
        fare_basis_codes=["OLN7AHM1"],
        validating_carrier="AA",
        pricing_modifier="TEST-MODIFIER",
        branded_components=[
            BrandedComponent(
                component_ref=1,
                begin_airport="EZE",
                end_airport="MIA",
                fare_basis_code="OLN7AHM1",
                governing_carrier="AA",
                brand_code="MAINPL",
                brand_name="MAIN PLUS",
                program_code="TEST",
            )
        ],
        brand_features=[
            BrandFeature(
                application="F",
                commercial_name="CHECKED BAG",
            )
        ],
    )

    payload = fare.model_dump(mode="json")
    restored = CommercialFare.model_validate(payload)

    assert restored.brand_code == "MAINPL"
    assert restored.pricing_modifier == "TEST-MODIFIER"
    assert restored.branded_components[0].brand_code == "MAINPL"
    assert restored.branded_components[0].fare_basis_code == "OLN7AHM1"
    assert restored.brand_features[0].commercial_name == "CHECKED BAG"


def test_fare_identity_inspector_is_read_only_and_surfaces_brand_fields() -> None:
    script = Path("scripts/inspect_booking_fare_identity.py").read_text(
        encoding="utf-8"
    )

    assert "brand_code=" in script
    assert "brand_name=" in script
    assert "pricing_modifier=" in script
    assert "fare_basis_codes=" in script
    assert "segment_booking_classes=" in script
    assert "branded_component_count=" in script
    assert "REQUIRES_CERT_PROOF" in script
    assert "no Sabre request was sent" in script
