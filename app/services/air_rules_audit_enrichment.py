from __future__ import annotations

import re
from dataclasses import replace

from app.models.api import FareRuleDatum, FareRuleFareAudit
from app.sabre.air_rules import AirRulesParsedResponse


def _category_16_text(parsed: AirRulesParsedResponse) -> str | None:
    texts = [
        category.text.strip()
        for category in parsed.categories
        if category.number == 16 and category.text.strip()
    ]
    return "\n".join(texts) if texts else None


def _changes_from_penalties(text: str) -> FareRuleDatum | None:
    upper = text.upper()

    if re.search(
        r"CHANGES?\s+(?:ARE\s+)?NOT\s+PERMITTED|"
        r"CHANGES?\s+NOT\s+ALLOWED|"
        r"NO\s+CHANGES?",
        upper,
    ):
        return FareRuleDatum(
            status="not_allowed",
            source="air_rules",
            confidence="high",
            text="Cambios no permitidos según Air Fare Rules (categoría 16).",
        )

    if re.search(
        r"CHANGES?\s+PERMITTED.*(?:FEE|CHARGE|PENALTY)|"
        r"(?:FEE|CHARGE|PENALTY).*CHANGES?",
        upper,
        re.DOTALL,
    ):
        return FareRuleDatum(
            status="with_fee",
            source="air_rules",
            confidence="high",
            text="Cambios permitidos con penalidad según Air Fare Rules (categoría 16).",
        )

    if re.search(
        r"CHANGES?\s+PERMITTED|CHANGES?\s+ALLOWED",
        upper,
    ):
        return FareRuleDatum(
            status="allowed",
            source="air_rules",
            confidence="high",
            text="Cambios permitidos según Air Fare Rules (categoría 16).",
        )

    return None


def _refunds_from_penalties(text: str) -> FareRuleDatum | None:
    upper = text.upper()

    if re.search(
        r"(?:TICKET\s+IS\s+)?NON[- ]?REFUNDABLE|"
        r"REFUNDS?\s+(?:ARE\s+)?NOT\s+PERMITTED|"
        r"REFUNDS?\s+NOT\s+ALLOWED|"
        r"NO\s+REFUND",
        upper,
    ):
        return FareRuleDatum(
            status="not_allowed",
            source="air_rules",
            confidence="high",
            text="Devolución no permitida según Air Fare Rules (categoría 16).",
        )

    if re.search(
        r"REFUNDS?.*(?:FEE|CHARGE|PENALTY)|"
        r"(?:FEE|CHARGE|PENALTY).*REFUNDS?",
        upper,
        re.DOTALL,
    ):
        return FareRuleDatum(
            status="with_fee",
            source="air_rules",
            confidence="high",
            text="Devolución permitida con penalidad según Air Fare Rules (categoría 16).",
        )

    if re.search(
        r"REFUNDS?\s+PERMITTED|REFUNDS?\s+ALLOWED|"
        r"REFUND\s+BEFORE\s+DEPARTURE",
        upper,
    ):
        return FareRuleDatum(
            status="allowed",
            source="air_rules",
            confidence="high",
            text="Devolución permitida según Air Fare Rules (categoría 16).",
        )

    return None


def enrich_fare_audit_with_air_rules(
    audit: FareRuleFareAudit,
    parsed: AirRulesParsedResponse,
) -> FareRuleFareAudit:
    """
    Enrich only when AirRules returned a successful Category 16 response.

    Unknown/ambiguous text leaves the existing BFM/branded conclusion intact.
    """
    if not parsed.success:
        return audit

    penalties = _category_16_text(parsed)
    if not penalties:
        return audit

    changes = _changes_from_penalties(penalties)
    refunds = _refunds_from_penalties(penalties)

    return audit.model_copy(
        update={
            "changes": changes or audit.changes,
            "refunds": refunds or audit.refunds,
        }
    )
