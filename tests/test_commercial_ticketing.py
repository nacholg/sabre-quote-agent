from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.models.api import FareRuleDatum, FareRuleFareAudit
from app.models.commercial_quote import CommercialFareRules
from app.services.commercial_renderer import _fare_lines
from app.services.fare_rule_commercial_summary import build_fare_rule_commercial_summary


ROOT = Path(__file__).resolve().parents[1]


def datum(
    text: str,
    *,
    status: str = "included",
    source: str = "ticketing",
    confidence: str = "high",
) -> FareRuleDatum:
    return FareRuleDatum(
        status=status,
        source=source,
        confidence=confidence,
        text=text,
    )


def audit(ticketing: FareRuleDatum) -> FareRuleFareAudit:
    return FareRuleFareAudit(
        cabin="economy",
        brand_name="MAIN CABIN",
        currency="USD",
        price_per_passenger=Decimal("100"),
        baggage=datum("1 pieza despachada.", source="baggage"),
        changes=datum("Cambios permitidos.", source="brand_feature"),
        refunds=datum(
            "Devolución no permitida.",
            status="not_allowed",
            source="fare_flag",
        ),
        ticketing=ticketing,
    )


def test_ticketing_summary_formats_iso_deadline_for_internal_use():
    summary = build_fare_rule_commercial_summary(
        audit(
            datum(
                "Emitir hasta el 2027-01-18 o antes si cambia la disponibilidad."
            )
        )
    )

    assert summary.ticketing == (
        "Emitir hasta el 18/01/2027 o antes si cambia la disponibilidad."
    )


def test_ticketing_summary_does_not_keep_bfm_internal_wording():
    summary = build_fare_rule_commercial_summary(
        audit(
            datum(
                "BFM no informó fecha límite de emisión para este producto.",
                status="unknown",
                source="not_provided",
                confidence="unknown",
            )
        )
    )

    assert "BFM" not in summary.ticketing
    assert "confirmar" in summary.ticketing.lower()


def test_commercial_fare_rules_can_store_ticketing_internally():
    rules = CommercialFareRules(
        ticketing="Emitir hasta el 18/01/2027."
    )
    assert rules.ticketing == "Emitir hasta el 18/01/2027."


def test_commercial_outputs_keep_ticketing_hidden():
    fare = SimpleNamespace(
        brand_name="MAIN CABIN",
        brand_code="MAIN",
        cabin="economy",
        currency="USD",
        price_per_passenger=100,
        q1_amount=None,
        q1_currency=None,
    )
    option = SimpleNamespace(is_domestic_argentina=False)
    summary = SimpleNamespace(
        baggage="1 pieza despachada.",
        changes="Cambios permitidos.",
        refunds="Devolución no permitida.",
        no_show="No-show: no permitido.",
        ticketing="Emitir hasta el 18/01/2027.",
    )

    rendered = "\n".join(
        _fare_lines(fare, option, commercial_summary=summary)
    )

    assert "Emisión:" not in rendered
    assert "Emitir hasta" not in rendered

    html = (ROOT / "app" / "web" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'fareRuleCommercialText(f, "ticketing", f.ticketing)' not in html
    assert 'commercialRuleLine("Emisión",rules.ticketing)' not in html
