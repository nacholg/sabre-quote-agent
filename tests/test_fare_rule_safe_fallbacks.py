from decimal import Decimal

from app.models.api import FareRuleDatum, FareRuleFareAudit
from app.services.fare_rule_commercial_summary import (
    build_fare_rule_commercial_summary,
)


def datum(status: str, text: str) -> FareRuleDatum:
    return FareRuleDatum(
        status=status,
        source="not_provided",
        confidence="unknown",
        text=text,
    )


def make_audit(*, changes: FareRuleDatum, refunds: FareRuleDatum) -> FareRuleFareAudit:
    return FareRuleFareAudit(
        cabin="economy",
        brand_name="TEST",
        currency="USD",
        price_per_passenger=Decimal("100"),
        baggage=datum("included", "1 pieza despachada."),
        changes=changes,
        refunds=refunds,
        ticketing=datum("unknown", "BFM no informó ticketing."),
        structured_details=None,
    )


def test_unknown_rules_do_not_expose_sabre_or_bfm_wording():
    result = build_fare_rule_commercial_summary(
        make_audit(
            changes=datum(
                "unknown",
                "BFM no informó una regla explícita para este concepto.",
            ),
            refunds=datum(
                "unknown",
                "Sabre no marcó la tarifa como no reembolsable.",
            ),
        )
    )

    joined = f"{result.changes} {result.refunds}".lower()
    assert "bfm" not in joined
    assert "sabre" not in joined
    assert result.changes == "Cambios: confirmar condiciones tarifarias."
    assert result.refunds == "Devoluciones: confirmar condiciones tarifarias."


def test_known_brand_statuses_get_client_safe_wording_without_structured_rules():
    result = build_fare_rule_commercial_summary(
        make_audit(
            changes=datum(
                "with_fee",
                "Disponible con cargo según atributo branded de Sabre.",
            ),
            refunds=datum(
                "not_allowed",
                "No permitido/no ofrecido según atributo branded de Sabre.",
            ),
        )
    )

    assert result.changes == (
        "Cambios permitidos con cargo; penalidad e importe a confirmar."
    )
    assert result.refunds == "Devolución no permitida."
    assert "Sabre" not in result.changes
    assert "Sabre" not in result.refunds


def test_allowed_change_fallback_does_not_claim_zero_fee():
    result = build_fare_rule_commercial_summary(
        make_audit(
            changes=datum(
                "included",
                "Incluido según atributo branded de Sabre.",
            ),
            refunds=datum(
                "allowed",
                "Devolución permitida según atributo branded de Sabre.",
            ),
        )
    )

    assert "sin cargo" not in result.changes.lower()
    assert "confirmar diferencia tarifaria" in result.changes.lower()
    assert result.refunds == "Devolución permitida según la tarifa."
