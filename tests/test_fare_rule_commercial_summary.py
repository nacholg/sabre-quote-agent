from decimal import Decimal

from app.models.api import (
    FareRuleCommercialSummary,
    FareRuleConditionDetail,
    FareRuleDatum,
    FareRuleFareAudit,
    FareRuleStructuredDetails,
)
from app.services.fare_rule_commercial_summary import (
    build_fare_rule_commercial_summary,
)


def datum(text):
    return FareRuleDatum(
        status="included",
        source="air_rules",
        confidence="high",
        text=text,
    )


def audit(*, refundable=False):
    return FareRuleFareAudit(
        cabin="economy",
        brand_name="MAIN CABIN FLEXIBLE" if refundable else "MAIN CABIN",
        currency="USD",
        price_per_passenger=Decimal("100"),
        baggage=datum("1 pieza despachada."),
        changes=datum("Cambios permitidos."),
        refunds=datum("Devolución permitida." if refundable else "No reembolsable."),
        ticketing=datum("Emitir antes del deadline."),
        structured_details=FareRuleStructuredDetails(
            changes_before_departure=FareRuleConditionDetail(
                status="allowed",
                fare_difference_applies=True,
            ),
            changes_after_departure=FareRuleConditionDetail(
                status="allowed",
                fare_difference_applies=True,
            ),
            cancellation_before_departure=FareRuleConditionDetail(
                status="allowed" if refundable else "not_allowed",
            ),
            cancellation_after_departure=FareRuleConditionDetail(
                status="allowed" if refundable else "not_allowed",
            ),
            no_show=FareRuleConditionDetail(
                status="allowed" if refundable else "not_allowed",
            ),
        ),
    )


def test_main_cabin_summary_is_commercially_clear():
    result = build_fare_rule_commercial_summary(audit())

    assert "antes y después de la salida" in result.changes
    assert "diferencia tarifaria" in result.changes
    assert result.refunds == "Devolución no permitida."
    assert "no permitido" in result.no_show


def test_flexible_summary_reflects_refund_and_no_show():
    result = build_fare_rule_commercial_summary(
        audit(refundable=True)
    )

    assert result.refunds == (
        "Devolución permitida antes y después de la salida."
    )
    assert "permitido" in result.no_show


def test_null_amount_does_not_claim_zero_penalty():
    result = build_fare_rule_commercial_summary(
        audit(refundable=True)
    )

    assert "sin penalidad" not in result.changes.lower()
    assert "USD 0" not in result.changes
