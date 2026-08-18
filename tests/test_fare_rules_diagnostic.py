from decimal import Decimal

from app.models.itinerary import BrandedComponent, FareOption
from scripts.test_fare_rules import build_candidate_xml, component_dict


def test_component_dict_preserves_rule_metadata():
    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal("100"),
        validating_carrier="AR",
        branded_components=[
            BrandedComponent(
                component_ref=7,
                begin_airport="COR",
                end_airport="AEP",
                fare_basis_code="A123",
                governing_carrier="AR",
                vendor_code="ATP",
                tariff="123",
                rule_number="4567",
                brand_name="FLEX",
            )
        ],
    )
    item = component_dict(fare, 0)
    assert item["component_ref"] == 7
    assert item["governing_carrier"] == "AR"
    assert item["governing_carrier_source"] == "fare_component"
    assert item["fare_basis_code"] == "A123"
    assert item["rule_number"] == "4567"


def test_component_dict_marks_validating_carrier_fallback():
    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal("100"),
        validating_carrier="AA",
        branded_components=[
            BrandedComponent(
                begin_airport="EZE",
                end_airport="MIA",
                fare_basis_code="MAIN",
            )
        ],
    )
    item = component_dict(fare, 0)
    assert item["governing_carrier"] == "AA"
    assert item["governing_carrier_source"] == "validating_carrier_fallback"


def test_xml_is_explicitly_not_for_transmission():
    xml = build_candidate_xml(
        {
            "governing_carrier": "AR",
            "fare_basis_code": "A123",
            "begin_airport": "COR",
            "end_airport": "AEP",
            "vendor_code": "ATP",
            "tariff": "123",
            "rule_number": "4567",
        },
        16,
        "2026-09-19",
    )
    assert 'status="NOT_FOR_TRANSMISSION"' in xml
    assert "<FareBasis>A123</FareBasis>" in xml
    assert "<Rule>4567</Rule>" in xml
