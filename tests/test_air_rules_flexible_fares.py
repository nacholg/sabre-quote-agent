from decimal import Decimal

from app.models.api import FareRuleDatum, FareRuleFareAudit
from app.sabre.air_rules import AirRulesCategory, AirRulesParsedResponse
from app.services.air_rules_audit_enrichment import (
    enrich_fare_audit_with_air_rules,
)


def _audit():
    unknown = FareRuleDatum(
        status="unknown",
        source="not_provided",
        confidence="unknown",
        text="unknown",
    )
    return FareRuleFareAudit(
        cabin="economy",
        brand_name="MAIN CABIN FLEXIBLE",
        brand_code="MAINFL",
        currency="USD",
        price_per_passenger=Decimal("1419.43"),
        baggage=unknown,
        changes=unknown,
        refunds=unknown,
        ticketing=unknown,
    )


def _parsed(text):
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


def test_aa_flexible_cancellations_and_changes_are_permitted():
    text = """CANCELLATIONS
ANY TIME
CANCELLATIONS PERMITTED FOR CANCEL/NO-SHOW.
CANCELLATIONS ARE PERMITTED WITHIN TICKET VALIDITY
OF ORIGINAL TICKET.
FOR CANCELLATION AFTER DEPARTURE THE REFUND WILL
BE THE DIFFERENCE BETWEEN FARE PAID AND FARE FOR
JOURNEY TRAVELLED.
FOR TRAVEL AGENCY BOOKINGS MADE IN CENTRAL AND SOUTH AMERICA -
AA WILL ASSESS A USD 50.00 FEE ON ANY UNTICKETED
RESERVATION NOT CANCELLED BEFORE DEPARTURE.
CHANGES
ANY TIME
CHANGES PERMITTED FOR NO-SHOW/REISSUE/REVALIDATION.
WHEN THE NEW ITINERARY RESULTS IN A HIGHER FARE
THE DIFFERENCE WILL BE COLLECTED.
"""

    result = enrich_fare_audit_with_air_rules(
        _audit(),
        _parsed(text),
    )

    assert result.changes.status in {"allowed", "with_fee"}
    assert result.refunds.status == "allowed"

    if hasattr(result, "refunds_penalty"):
        assert result.refunds_penalty is None


def test_non_refundable_still_wins():
    text = """CANCELLATIONS
ANY TIME
TICKET IS NON-REFUNDABLE IN CASE OF CANCEL/NO-SHOW/REFUND.
CHANGES
CHANGES PERMITTED.
"""

    result = enrich_fare_audit_with_air_rules(
        _audit(),
        _parsed(text),
    )

    assert result.refunds.status == "not_allowed"
