from decimal import Decimal

from app.models.itinerary import BrandedComponent, FareOption
from app.services.fare_rule_reliability import audit_fare


def _fare(**updates) -> FareOption:
    payload = dict(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal("800.00"),
        total_price=Decimal("800.00"),
        brand_name="FLEX",
        brand_code="FX",
        fare_basis_codes=["ole0n5b", "OLE0N5B", "mlx1a2c"],
    )
    payload.update(updates)
    return FareOption(**payload)


def test_audit_fare_exposes_deduplicated_fare_basis_codes():
    audit = audit_fare(_fare())

    assert audit.fare_basis_codes == ["OLE0N5B", "MLX1A2C"]
    assert audit.fare_components == []


def test_audit_fare_exposes_route_specific_fare_components():
    fare = _fare(
        branded_components=[
            BrandedComponent(
                begin_airport="EZE",
                end_airport="MIA",
                fare_basis_code="ole0n5b",
                governing_carrier="AA",
                vendor_code="ATP",
                tariff="001",
                rule_number="1234",
                brand_code="FX",
                brand_name="FLEX",
            ),
            BrandedComponent(
                begin_airport="MIA",
                end_airport="EZE",
                fare_basis_code="mlx1a2c",
                governing_carrier="AA",
                tariff="002",
                rule_number="5678",
            ),
        ]
    )

    audit = audit_fare(fare)

    assert audit.fare_basis_codes == ["OLE0N5B", "MLX1A2C"]
    assert len(audit.fare_components) == 2

    outbound = audit.fare_components[0]
    assert outbound.begin_airport == "EZE"
    assert outbound.end_airport == "MIA"
    assert outbound.fare_basis_code == "OLE0N5B"
    assert outbound.governing_carrier == "AA"
    assert outbound.rule_number == "1234"
    assert outbound.tariff == "001"


def test_component_fare_basis_is_included_even_if_missing_from_flat_list():
    audit = audit_fare(
        _fare(
            fare_basis_codes=[],
            branded_components=[
                BrandedComponent(
                    begin_airport="EZE",
                    end_airport="MAD",
                    fare_basis_code="yk7nr",
                )
            ],
        )
    )

    assert audit.fare_basis_codes == ["YK7NR"]
    assert audit.fare_components[0].fare_basis_code == "YK7NR"
