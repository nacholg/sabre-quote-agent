from decimal import Decimal

from app.models.api import FareRuleDatum, FareRuleFareAudit
from app.sabre.air_rules import AirRulesCategory, AirRulesParsedResponse
from app.services.air_rules_audit_enrichment import (
    enrich_fare_audit_with_air_rules,
)


def audit():
    unknown = FareRuleDatum(
        status="unknown",
        source="not_provided",
        confidence="unknown",
        text="unknown",
    )
    return FareRuleFareAudit(
        cabin="economy",
        brand_name="MAIN",
        currency="USD",
        price_per_passenger=Decimal("100"),
        baggage=unknown,
        changes=unknown,
        refunds=unknown,
        ticketing=unknown,
    )


def parsed(text):
    return AirRulesParsedResponse(
        success=True,
        categories=(
            AirRulesCategory(number=16, title="PENALTIES", text=text),
        ),
    )


def test_change_penalty_extracts_amount_and_currency():
    text = (
        "CANCELLATIONS\n"
        "REFUND PERMITTED.\n"
        "CHANGES\n"
        "CHANGES PERMITTED.\n"
        "CHANGE FEE USD 200.00.\n"
    )
    result = enrich_fare_audit_with_air_rules(audit(), parsed(text))
    assert result.changes.status == "with_fee"
    assert result.changes_penalty.currency == "USD"
    assert result.changes_penalty.amount == Decimal("200.00")


def test_refund_penalty_extracts_amount_and_currency():
    text = (
        "CANCELLATIONS\n"
        "REFUND PERMITTED.\n"
        "CANCELLATION FEE EUR 150.00.\n"
        "CHANGES\n"
        "CHANGES PERMITTED.\n"
    )
    result = enrich_fare_audit_with_air_rules(audit(), parsed(text))
    assert result.refunds.status == "with_fee"
    assert result.refunds_penalty.currency == "EUR"
    assert result.refunds_penalty.amount == Decimal("150.00")


def test_agency_unticketed_fee_is_not_refund_penalty():
    text = (
        "CANCELLATIONS\n"
        "TICKET IS NON-REFUNDABLE.\n"
        "FOR TRAVEL AGENCY BOOKINGS AA WILL ASSESS A USD 50.00 FEE "
        "ON ANY UNTICKETED RESERVATION NOT CANCELLED BEFORE DEPARTURE.\n"
        "CHANGES\n"
        "CHANGES PERMITTED.\n"
    )
    result = enrich_fare_audit_with_air_rules(audit(), parsed(text))
    assert result.refunds.status == "not_allowed"
    assert result.refunds_penalty is None


def test_fare_difference_is_not_change_penalty():
    text = (
        "CANCELLATIONS\n"
        "TICKET IS NON-REFUNDABLE.\n"
        "CHANGES\n"
        "CHANGES PERMITTED.\n"
        "WHEN THE NEW ITINERARY RESULTS IN A HIGHER FARE "
        "THE DIFFERENCE WILL BE COLLECTED.\n"
    )
    result = enrich_fare_audit_with_air_rules(audit(), parsed(text))
    assert result.changes.status == "allowed"
    assert result.changes_penalty is None
    assert result.change_fare_difference_applies is True
