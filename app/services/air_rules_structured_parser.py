from __future__ import annotations

import re
from decimal import Decimal

from app.models.api import (
    FareRuleConditionDetail,
    FareRuleStructuredDetails,
)

_MONEY = re.compile(
    r"\b(?P<currency>USD|EUR|ARS|GBP|CAD|AUD|BRL|MXN)\s*"
    r"(?P<amount>\d+(?:[.,]\d{1,2})?)\b",
    re.IGNORECASE,
)


def _money_near(text: str, keywords: tuple[str, ...]):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        match = _MONEY.search(line)
        if not match:
            continue

        start = max(0, idx - 2)
        end = min(len(lines), idx + 3)
        context = " ".join(lines[start:end]).upper()

        if any(
            excluded in context
            for excluded in (
                "UNTICKETED RESERVATION",
                "TRAVEL AGENCY BOOKING",
                "TRAVEL AGENCY BOOKINGS",
                "FARE DIFFERENCE",
                "DIFFERENCE IN FARE",
                "HIGHER FARE",
                "LOWER FARE",
                "TRAVEL VOUCHER",
            )
        ):
            continue

        if any(keyword in context for keyword in keywords):
            return (
                Decimal(match.group("amount").replace(",", ".")),
                match.group("currency").upper(),
            )
    return None


def _fare_difference(text: str) -> bool | None:
    upper = text.upper()
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


def _status_change(text: str) -> str:
    upper = text.upper()
    if re.search(
        r"\bCHANGES?\s+(?:ARE\s+)?NOT\s+PERMITTED\b|"
        r"\bCHANGES?\s+NOT\s+ALLOWED\b|"
        r"\bCHANGES?\s+PROHIBITED\b",
        upper,
    ):
        return "not_allowed"
    if re.search(
        r"\bCHANGES?\s+PERMITTED\b|"
        r"\bCHANGES?\s+ALLOWED\b|"
        r"\bCHANGE IS PERMITTED\b",
        upper,
    ):
        return "allowed"
    return "unknown"


def _status_cancel(text: str) -> str:
    upper = text.upper()
    if re.search(
        r"NON[- ]?REFUNDABLE|"
        r"\bNO\s+REFUND\b|"
        r"REFUNDS?\s+(?:ARE\s+)?NOT\s+PERMITTED|"
        r"CANCELLATIONS?\s+(?:ARE\s+)?NOT\s+PERMITTED",
        upper,
    ):
        return "not_allowed"
    if re.search(
        r"CANCELLATIONS?\s+PERMITTED|"
        r"CANCELLATIONS?\s+ARE\s+PERMITTED|"
        r"REFUNDS?\s+PERMITTED|"
        r"REFUNDS?\s+ALLOWED|"
        r"\bREFUNDABLE\b",
        upper,
    ):
        return "allowed"
    return "unknown"


def _detail(text: str, kind: str):
    if not text.strip():
        return None

    status = _status_change(text) if kind == "change" else _status_cancel(text)

    keywords = (
        (
            "CHANGE FEE",
            "CHANGE CHARGE",
            "CHANGE PENALTY",
            "REISSUE FEE",
            "REISSUE CHARGE",
            "REISSUE PENALTY",
            "EXCHANGE FEE",
            "EXCHANGE CHARGE",
            "EXCHANGE PENALTY",
            "FEE FOR CHANGE",
            "PENALTY FOR CHANGE",
        )
        if kind == "change"
        else (
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
            "PENALTY FOR REFUND",
        )
    )

    money = _money_near(text, keywords)
    amount = None
    currency = None
    if money:
        amount, currency = money
        if status == "allowed":
            status = "with_fee"

    return FareRuleConditionDetail(
        status=status,
        amount=amount,
        currency=currency,
        fare_difference_applies=_fare_difference(text) if kind == "change" else None,
        source_text=text.strip()[:3000],
    )


def parse_category_16_structured(text: str) -> FareRuleStructuredDetails:
    upper = text.upper()
    changes_match = re.search(r"(?m)^\s*CHANGES\s*$", upper)
    cancellations_match = re.search(r"(?m)^\s*CANCELLATIONS\s*$", upper)

    cancellations = ""
    changes = ""

    if cancellations_match:
        start = cancellations_match.end()
        end = changes_match.start() if changes_match else len(text)
        cancellations = text[start:end].strip()

    if changes_match:
        changes = text[changes_match.end():].strip()

    def before_after(block: str):
        b_upper = block.upper()
        before = ""
        after = ""

        before_match = re.search(r"(?m)^\s*BEFORE DEPARTURE\s*$", b_upper)
        after_match = re.search(r"(?m)^\s*AFTER DEPARTURE\s*$", b_upper)
        anytime_match = re.search(r"(?m)^\s*ANY TIME\s*$", b_upper)

        if before_match:
            start = before_match.end()
            end = after_match.start() if after_match else len(block)
            before = block[start:end].strip()

        if after_match:
            after = block[after_match.end():].strip()

        if not before and not after and anytime_match:
            anytime = block[anytime_match.end():].strip()
            before = anytime
            after = anytime

        if not before and not after and block:
            before = block
            after = block

        return before, after

    cancel_before, cancel_after = before_after(cancellations)
    change_before, change_after = before_after(changes)

    no_show_lines = []
    for block in (cancellations, changes):
        no_show_lines.extend(
            line for line in block.splitlines()
            if "NO-SHOW" in line.upper() or "NO SHOW" in line.upper()
        )
    no_show_text = "\n".join(no_show_lines)

    return FareRuleStructuredDetails(
        changes_before_departure=_detail(change_before, "change"),
        changes_after_departure=_detail(change_after, "change"),
        cancellation_before_departure=_detail(cancel_before, "cancel"),
        cancellation_after_departure=_detail(cancel_after, "cancel"),
        no_show=_detail(no_show_text, "cancel") if no_show_text else None,
    )
