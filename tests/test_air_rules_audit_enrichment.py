from decimal import Decimal

from app.models.api import FareRuleDatum, FareRuleFareAudit
from app.sabre.air_rules import AirRulesCategory, AirRulesParsedResponse
from app.services.air_rules_audit_enrichment import enrich_fare_audit_with_air_rules


def base_audit() -> FareRuleFareAudit:
    unknown = FareRuleDatum(
        status="unknown",
        source="not_provided",
        confidence="unknown",
        text="Sin dato.",
    )
    baggage = FareRuleDatum(
        status="included",
        source="baggage",
        confidence="high",
        text="1 pieza.",
    )
    ticketing = FareRuleDatum(
        status="unknown",
        source="not_provided",
        confidence="unknown",
        text="Sin dato.",
    )
    return FareRuleFareAudit(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal("500"),
        baggage=baggage,
        changes=unknown,
        refunds=unknown,
        ticketing=ticketing,
    )


def parsed(text: str) -> AirRulesParsedResponse:
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


def test_enriches_changes_with_fee_and_refund_not_allowed():
    result = enrich_fare_audit_with_air_rules(
        base_audit(),
        parsed(
            "CHANGES PERMITTED WITH FEE USD 200. "
            "TICKET IS NON-REFUNDABLE."
        ),
    )

    assert result.changes.status == "with_fee"
    assert result.changes.source == "air_rules"
    assert result.changes.confidence == "high"

    assert result.refunds.status == "not_allowed"
    assert result.refunds.source == "air_rules"


def test_enriches_allowed_refund_when_explicit():
    result = enrich_fare_audit_with_air_rules(
        base_audit(),
        parsed("REFUND BEFORE DEPARTURE USD 300."),
    )

    assert result.refunds.status == "allowed"
    assert result.refunds.source == "air_rules"


def test_ambiguous_category_16_keeps_existing_bfm_values():
    original = base_audit().model_copy(
        update={
            "changes": FareRuleDatum(
                status="with_fee",
                source="brand_feature",
                confidence="high",
                text="Con cargo branded.",
            )
        }
    )

    result = enrich_fare_audit_with_air_rules(
        original,
        parsed("SEE GENERAL RULE."),
    )

    assert result.changes == original.changes
    assert result.refunds == original.refunds


def test_failed_air_rules_response_does_not_override_audit():
    original = base_audit()

    result = enrich_fare_audit_with_air_rules(
        original,
        AirRulesParsedResponse(
            success=False,
            categories=(),
            fault_string="Authentication failed",
        ),
    )

    assert result == original


def test_other_categories_do_not_override_audit():
    original = base_audit()

    result = enrich_fare_audit_with_air_rules(
        original,
        AirRulesParsedResponse(
            success=True,
            categories=(
                AirRulesCategory(
                    number=5,
                    title="ADVANCE RESERVATIONS",
                    text="TICKETING 7 DAYS BEFORE DEPARTURE.",
                ),
            ),
        ),
    )

    assert result == original
