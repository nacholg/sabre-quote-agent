from app.models.api import StoredQuoteRecord
from app.sabre.air_rules import AirRulesCategory, AirRulesParsedResponse
from app.services.fare_rule_reliability import audit_stored_quote


def record() -> StoredQuoteRecord:
    return StoredQuoteRecord(
        quote_id="Q1",
        created_at="2026-08-18T00:00:00",
        updated_at="2026-08-18T00:00:00",
        status="active",
        selected_ranks=[1],
        source="agent",
        search_request={},
        quote_response={
            "options": [
                {
                    "rank": 1,
                    "itinerary": {
                        "segments": [
                            {
                                "marketing_carrier": "AA",
                                "flight_number": "908",
                                "departure_airport": "EZE",
                                "arrival_airport": "MIA",
                                "departure_at": "2027-02-10T23:00:00-03:00",
                                "arrival_at": "2027-02-11T06:55:00-05:00",
                            }
                        ],
                        "fare": {
                            "cabin": "economy",
                            "currency": "USD",
                            "price_per_passenger": "500",
                            "fare_basis_codes": ["OLX0N1M1"],
                        },
                        "fares_by_currency": {
                            "USD": {
                                "cabin": "economy",
                                "currency": "USD",
                                "price_per_passenger": "500",
                                "fare_basis_codes": ["OLX0N1M1"],
                            }
                        },
                    },
                }
            ]
        },
    )


def penalties(text: str) -> AirRulesParsedResponse:
    return AirRulesParsedResponse(
        success=True,
        categories=(
            AirRulesCategory(
                number=16,
                title="PENALTIES",
                text=text,
            ),
        ),
    )


def test_audit_uses_air_rules_map_when_available():
    result = audit_stored_quote(
        record(),
        air_rules_by_fare_basis={
            "OLX0N1M1": penalties(
                "CHANGES PERMITTED WITH FEE USD 200. "
                "TICKET IS NON-REFUNDABLE."
            )
        },
    )

    fare = result.options[0].fares[0]
    assert fare.changes.source == "air_rules"
    assert fare.changes.status == "with_fee"
    assert fare.refunds.source == "air_rules"
    assert fare.refunds.status == "not_allowed"
    assert result.requires_external_rule_lookup is False
    assert result.external_rule_lookup_status == "resolved"


def test_audit_without_air_rules_remains_backward_compatible():
    result = audit_stored_quote(record())

    fare = result.options[0].fares[0]
    assert fare.changes.source == "not_provided"
    assert result.requires_external_rule_lookup is True
    assert result.external_rule_lookup_status == "pending_authentication"
