from decimal import Decimal

from app.models.api import (
    FareRuleConditionDetail,
    FareRuleDatum,
    FareRuleFareAudit,
    FareRuleStructuredDetails,
)
from app.services.fare_rule_commercial_summary import (
    build_fare_rule_commercial_summary,
)


def datum(text: str) -> FareRuleDatum:
    return FareRuleDatum(
        status="included",
        source="air_rules",
        confidence="high",
        text=text,
    )


def audit(details: FareRuleStructuredDetails) -> FareRuleFareAudit:
    return FareRuleFareAudit(
        cabin="economy",
        brand_name="TEST",
        currency="USD",
        price_per_passenger=Decimal("100"),
        baggage=datum("1 pieza."),
        changes=datum("Cambios permitidos."),
        refunds=datum("Devolución permitida."),
        ticketing=datum("Emitir según deadline."),
        structured_details=details,
    )


def test_changes_show_before_and_after_penalties_separately():
    result = build_fare_rule_commercial_summary(
        audit(
            FareRuleStructuredDetails(
                changes_before_departure=FareRuleConditionDetail(
                    status="with_fee",
                    amount=Decimal("100"),
                    currency="USD",
                    fare_difference_applies=True,
                ),
                changes_after_departure=FareRuleConditionDetail(
                    status="with_fee",
                    amount=Decimal("200"),
                    currency="USD",
                    fare_difference_applies=True,
                ),
            )
        )
    )

    assert "antes de la salida: penalidad USD 100.00" in result.changes
    assert "después de la salida: penalidad USD 200.00" in result.changes
    assert "USD 100.00 / USD 200.00" not in result.changes
    assert "diferencia tarifaria" in result.changes


def test_refunds_show_different_before_after_conditions_separately():
    result = build_fare_rule_commercial_summary(
        audit(
            FareRuleStructuredDetails(
                cancellation_before_departure=FareRuleConditionDetail(
                    status="allowed",
                ),
                cancellation_after_departure=FareRuleConditionDetail(
                    status="with_fee",
                    amount=Decimal("150"),
                    currency="USD",
                ),
            )
        )
    )

    assert "antes de la salida: permitido" in result.refunds
    assert "después de la salida: penalidad USD 150.00" in result.refunds


def test_same_penalty_keeps_compact_commercial_wording():
    result = build_fare_rule_commercial_summary(
        audit(
            FareRuleStructuredDetails(
                changes_before_departure=FareRuleConditionDetail(
                    status="with_fee",
                    amount=Decimal("100"),
                    currency="USD",
                ),
                changes_after_departure=FareRuleConditionDetail(
                    status="with_fee",
                    amount=Decimal("100"),
                    currency="USD",
                ),
            )
        )
    )

    assert "penalidad identificada: USD 100.00" in result.changes
    assert "antes de la salida: penalidad" not in result.changes
