from decimal import Decimal

from app.models.api import FareRuleDatum, FareRuleFareAudit
from app.sabre.air_rules import AirRulesCategory, AirRulesParsedResponse
from app.services.air_rules_audit_enrichment import (
    enrich_fare_audit_with_air_rules,
)


def base_audit() -> FareRuleFareAudit:
    unknown = FareRuleDatum(
        status="unknown",
        source="not_provided",
        confidence="unknown",
        text="unknown",
    )
    return FareRuleFareAudit(
        cabin="economy",
        brand_name="MAIN CABIN",
        brand_code="MAIN",
        currency="USD",
        price_per_passenger=Decimal("1151.93"),
        baggage=unknown,
        changes=unknown,
        refunds=unknown,
        ticketing=unknown,
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


def test_aa_no_changes_phrase_does_not_mean_changes_prohibited():
    text = """CANCELLATIONS
ANY TIME
TICKET IS NON-REFUNDABLE IN CASE OF CANCEL/NO-SHOW/REFUND.
FOR TRAVEL AGENCY BOOKINGS AA WILL ASSESS A USD 50.00 FEE.
CHANGES
BEFORE DEPARTURE
CHANGES PERMITTED.
WHEN THERE ARE NO CHANGES TO THE FIRST FARE COMPONENT BUT OTHER
FARE COMPONENTS ARE CHANGED THE ITINERARY MUST BE REPRICED.
AFTER DEPARTURE
CHANGES PERMITTED.
"""

    result = enrich_fare_audit_with_air_rules(
        base_audit(),
        parsed(text),
    )

    assert result.changes.status == "allowed"
    assert result.changes.source == "air_rules"
    assert result.refunds.status == "not_allowed"
    assert result.refunds.source == "air_rules"


def test_cancellation_fee_does_not_become_change_fee():
    text = """CANCELLATIONS
ANY TIME
REFUND PERMITTED.
CANCELLATION FEE USD 100.
CHANGES
BEFORE DEPARTURE
CHANGES PERMITTED.
AFTER DEPARTURE
CHANGES PERMITTED.
"""

    result = enrich_fare_audit_with_air_rules(
        base_audit(),
        parsed(text),
    )

    assert result.changes.status == "allowed"
    assert result.refunds.status == "with_fee"


def test_explicit_changes_not_permitted_remains_not_allowed():
    text = """CANCELLATIONS
TICKET IS NON-REFUNDABLE.
CHANGES
BEFORE DEPARTURE
CHANGES NOT PERMITTED.
"""

    result = enrich_fare_audit_with_air_rules(
        base_audit(),
        parsed(text),
    )

    assert result.changes.status == "not_allowed"
    assert result.refunds.status == "not_allowed"
