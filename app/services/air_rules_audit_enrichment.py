from __future__ import annotations

import re
from decimal import Decimal

from app.models.api import FareRuleDatum, FareRuleFareAudit, FareRulePenalty
from app.sabre.air_rules import AirRulesParsedResponse
from app.services.air_rules_structured_parser import parse_category_16_structured
from app.services.fare_rule_commercial_summary import build_fare_rule_commercial_summary


_MONEY_RE = re.compile(
    r"\b(?P<currency>USD|EUR|ARS|GBP|CAD|AUD|BRL|MXN)\s*"
    r"(?P<amount>\d+(?:[.,]\d{1,2})?)\b",
    re.IGNORECASE,
)


def _category_16_text(parsed: AirRulesParsedResponse) -> str | None:
    texts = [
        category.text.strip()
        for category in parsed.categories
        if category.number == 16 and category.text.strip()
    ]
    return "\n".join(texts) if texts else None


def _section(text: str, start: str, stops: tuple[str, ...]) -> str:
    upper = text.upper()
    match = re.search(rf"(?m)^\s*{re.escape(start)}\s*$", upper)
    if not match:
        return ""

    tail = text[match.end():]
    tail_upper = upper[match.end():]

    end_positions = []
    for stop in stops:
        stop_match = re.search(rf"(?m)^\s*{re.escape(stop)}\s*$", tail_upper)
        if stop_match:
            end_positions.append(stop_match.start())

    if end_positions:
        tail = tail[:min(end_positions)]

    return tail.strip()


def _money_candidates(section: str):
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    found = []

    for idx, line in enumerate(lines):
        for match in _MONEY_RE.finditer(line):
            currency = match.group("currency").upper()
            amount = Decimal(match.group("amount").replace(",", "."))
            start = max(0, idx - 2)
            end = min(len(lines), idx + 3)
            context = " ".join(lines[start:end]).upper()
            found.append((amount, currency, context))

    return found


def _excluded_money_context(context: str) -> bool:
    exclusions = (
        "UNTICKETED RESERVATION",
        "TRAVEL AGENCY BOOKING",
        "TRAVEL AGENCY BOOKINGS",
        "AGENCY BOOKING",
        "AGENCY BOOKINGS",
        "FARE DIFFERENCE",
        "DIFFERENCE IN FARE",
        "HIGHER FARE",
        "LOWER FARE",
        "TRAVEL VOUCHER",
        "ADDITIONAL COLLECTION",
    )
    return any(term in context for term in exclusions)


def _change_penalty(section: str) -> FareRulePenalty | None:
    phrases = (
        "CHANGE FEE",
        "CHANGE CHARGE",
        "CHANGE PENALTY",
        "REISSUE FEE",
        "REISSUE CHARGE",
        "REISSUE PENALTY",
        "EXCHANGE FEE",
        "EXCHANGE CHARGE",
        "EXCHANGE PENALTY",
        "CHANGES PERMITTED WITH FEE",
        "CHANGES PERMITTED WITH CHARGE",
        "CHANGES PERMITTED WITH PENALTY",
        "FEE FOR CHANGE",
        "CHARGE FOR CHANGE",
        "PENALTY FOR CHANGE",
    )

    for amount, currency, context in _money_candidates(section):
        if _excluded_money_context(context):
            continue
        if any(phrase in context for phrase in phrases):
            return FareRulePenalty(
                amount=amount,
                currency=currency,
                text="Penalidad de cambio identificada en Category 16.",
            )
    return None


def _refund_penalty(section: str) -> FareRulePenalty | None:
    phrases = (
        "REFUND FEE",
        "REFUND CHARGE",
        "REFUND PENALTY",
        "CANCELLATION FEE",
        "CANCELLATION CHARGE",
        "CANCELLATION PENALTY",
        "CANCEL FEE",
        "CANCEL CHARGE",
        "CANCEL PENALTY",
        "FEE FOR REFUND",
        "CHARGE FOR REFUND",
        "PENALTY FOR REFUND",
        "FEE FOR CANCELLATION",
        "CHARGE FOR CANCELLATION",
        "PENALTY FOR CANCELLATION",
    )

    for amount, currency, context in _money_candidates(section):
        if _excluded_money_context(context):
            continue
        if any(phrase in context for phrase in phrases):
            return FareRulePenalty(
                amount=amount,
                currency=currency,
                text="Penalidad de devolución/cancelación identificada en Category 16.",
            )
    return None


def _fare_difference_applies(section: str) -> bool | None:
    upper = section.upper()
    if not upper:
        return None

    if re.search(
        r"HIGHER FARE.*(?:COLLECTED|APPLIES)|"
        r"FARE DIFFERENCE.*(?:COLLECTED|APPLIES)|"
        r"DIFFERENCE IN FARE.*(?:COLLECTED|APPLIES)",
        upper,
        re.DOTALL,
    ):
        return True

    if re.search(r"NO FARE DIFFERENCE|FARE DIFFERENCE DOES NOT APPLY", upper):
        return False

    return None


def _changes_from_penalties(text: str):
    section = _section(text, "CHANGES", ("CANCELLATIONS", "REFUNDS"))
    working = section or text
    upper = working.upper()

    if re.search(
        r"\bCHANGES?\s+(?:ARE\s+)?NOT\s+PERMITTED\b|"
        r"\bCHANGES?\s+NOT\s+ALLOWED\b|"
        r"\bCHANGES?\s+PROHIBITED\b",
        upper,
    ):
        return (
            FareRuleDatum(
                status="not_allowed",
                source="air_rules",
                confidence="high",
                text="Cambios no permitidos según Air Fare Rules (categoría 16).",
            ),
            None,
            _fare_difference_applies(working),
        )

    permitted = bool(
        re.search(r"\bCHANGES?\s+PERMITTED\b|\bCHANGES?\s+ALLOWED\b", upper)
    )
    if not permitted:
        return None, None, _fare_difference_applies(working)

    penalty = _change_penalty(working)
    if penalty:
        status = FareRuleDatum(
            status="with_fee",
            source="air_rules",
            confidence="high",
            text=(
                f"Cambios permitidos con penalidad "
                f"{penalty.currency} {penalty.amount:.2f} según "
                "Air Fare Rules (categoría 16)."
            ),
        )
    else:
        status = FareRuleDatum(
            status="allowed",
            source="air_rules",
            confidence="high",
            text="Cambios permitidos según Air Fare Rules (categoría 16).",
        )

    return status, penalty, _fare_difference_applies(working)


def _refunds_from_penalties(text: str):
    section = _section(text, "CANCELLATIONS", ("CHANGES", "REFUNDS"))
    if not section:
        section = _section(text, "REFUNDS", ("CHANGES", "CANCELLATIONS"))

    working = section or text
    upper = working.upper()

    if re.search(
        r"\b(?:TICKET\s+IS\s+)?NON[- ]?REFUNDABLE\b|"
        r"\bREFUNDS?\s+(?:ARE\s+)?NOT\s+PERMITTED\b|"
        r"\bREFUNDS?\s+NOT\s+ALLOWED\b|"
        r"\bNO\s+REFUND\b",
        upper,
    ):
        return (
            FareRuleDatum(
                status="not_allowed",
                source="air_rules",
                confidence="high",
                text="Devolución no permitida según Air Fare Rules (categoría 16).",
            ),
            None,
        )

    allowed = bool(
        re.search(
            r"\bCANCELLATIONS?\s+PERMITTED\b|"
        r"\bCANCELLATIONS?\s+ALLOWED\b|"
        r"\bREFUNDS?\s+PERMITTED\b|"
            r"\bREFUNDS?\s+ALLOWED\b|"
            r"\bREFUND\s+BEFORE\s+DEPARTURE\b|"
            r"\bREFUNDABLE\b",
            upper,
        )
    )
    if not allowed:
        return None, None

    penalty = _refund_penalty(working)
    if penalty:
        status = FareRuleDatum(
            status="with_fee",
            source="air_rules",
            confidence="high",
            text=(
                f"Devolución permitida con penalidad "
                f"{penalty.currency} {penalty.amount:.2f} según "
                "Air Fare Rules (categoría 16)."
            ),
        )
    else:
        status = FareRuleDatum(
            status="allowed",
            source="air_rules",
            confidence="high",
            text="Devolución permitida según Air Fare Rules (categoría 16).",
        )

    return status, penalty


def enrich_fare_audit_with_air_rules(
    audit: FareRuleFareAudit,
    parsed: AirRulesParsedResponse,
) -> FareRuleFareAudit:
    if not parsed.success:
        return audit

    penalties = _category_16_text(parsed)
    if not penalties:
        return audit

    changes, changes_penalty, fare_difference = _changes_from_penalties(penalties)
    refunds, refunds_penalty = _refunds_from_penalties(penalties)

    enriched = audit.model_copy(
        update={
            "changes": changes or audit.changes,
            "refunds": refunds or audit.refunds,
            "structured_details": parse_category_16_structured(penalties),
            "changes_penalty": changes_penalty,
            "refunds_penalty": refunds_penalty,
            "change_fare_difference_applies": fare_difference,
        }
    )
    return enriched.model_copy(
        update={
            "commercial_summary": build_fare_rule_commercial_summary(enriched),
        }
    )
