from __future__ import annotations

import re

from app.models.api import (
    FareRuleCommercialSummary,
    FareRuleConditionDetail,
    FareRuleFareAudit,
)


def _money(detail: FareRuleConditionDetail | None) -> str | None:
    if not detail or detail.amount is None or not detail.currency:
        return None
    return f"{detail.currency} {detail.amount:.2f}"


def _period_condition_text(
    label: str,
    detail: FareRuleConditionDetail,
) -> str:
    money = _money(detail)

    if detail.status == "not_allowed":
        return f"{label}: no permitido"
    if money:
        return f"{label}: penalidad {money}"
    if detail.status == "with_fee":
        return (
            f"{label}: con penalidad, "
            "sin importe monetario identificado"
        )
    if detail.status == "allowed":
        return f"{label}: permitido"
    return f"{label}: condición no determinada"


def _different_period_conditions(
    before: FareRuleConditionDetail | None,
    after: FareRuleConditionDetail | None,
) -> list[str]:
    """Describe before/after separately only when their conditions differ."""
    if before is None or after is None:
        return []

    before_signature = (
        before.status,
        _money(before),
        before.fare_difference_applies,
    )
    after_signature = (
        after.status,
        _money(after),
        after.fare_difference_applies,
    )

    if before_signature == after_signature:
        return []

    return [
        _period_condition_text("antes de la salida", before),
        _period_condition_text("después de la salida", after),
    ]


def _changes_text(audit: FareRuleFareAudit) -> str:
    details = audit.structured_details
    if not details:
        return audit.changes.text

    before = details.changes_before_departure
    after = details.changes_after_departure

    statuses = {
        d.status
        for d in (before, after)
        if d is not None
    }

    if statuses and statuses <= {"not_allowed"}:
        return "Cambios no permitidos."

    if "allowed" in statuses or "with_fee" in statuses:
        amounts = {
            value
            for value in (_money(before), _money(after))
            if value
        }

        parts = ["Cambios permitidos"]

        if before and after:
            parts[0] += " antes y después de la salida"
        elif before:
            parts[0] += " antes de la salida"
        elif after:
            parts[0] += " después de la salida"

        period_conditions = _different_period_conditions(
            before,
            after,
        )

        if period_conditions:
            parts.extend(period_conditions)
        elif amounts:
            if len(amounts) == 1:
                parts.append(
                    f"penalidad identificada: {next(iter(amounts))}"
                )
            else:
                parts.append(
                    "penalidades identificadas: "
                    + " / ".join(sorted(amounts))
                )
        elif (
            (before and before.status == "with_fee")
            or (after and after.status == "with_fee")
        ):
            parts.append(
                "aplica penalidad, sin importe monetario identificado"
            )

        fare_difference = any(
            d and d.fare_difference_applies is True
            for d in (before, after)
        )
        if fare_difference:
            parts.append("aplica diferencia tarifaria")

        return "; ".join(parts) + "."

    return audit.changes.text


def _refunds_text(audit: FareRuleFareAudit) -> str:
    details = audit.structured_details
    if not details:
        return audit.refunds.text

    before = details.cancellation_before_departure
    after = details.cancellation_after_departure

    statuses = {
        d.status
        for d in (before, after)
        if d is not None
    }

    if statuses and statuses <= {"not_allowed"}:
        return "Devolución no permitida."

    if "allowed" in statuses or "with_fee" in statuses:
        amounts = {
            value
            for value in (_money(before), _money(after))
            if value
        }

        parts = ["Devolución permitida"]

        if before and after:
            parts[0] += " antes y después de la salida"
        elif before:
            parts[0] += " antes de la salida"
        elif after:
            parts[0] += " después de la salida"

        period_conditions = _different_period_conditions(
            before,
            after,
        )

        if period_conditions:
            parts.extend(period_conditions)
        elif amounts:
            if len(amounts) == 1:
                parts.append(
                    f"penalidad identificada: {next(iter(amounts))}"
                )
            else:
                parts.append(
                    "penalidades identificadas: "
                    + " / ".join(sorted(amounts))
                )
        elif (
            (before and before.status == "with_fee")
            or (after and after.status == "with_fee")
        ):
            parts.append(
                "aplica penalidad, sin importe monetario identificado"
            )

        return "; ".join(parts) + "."

    return audit.refunds.text


def _no_show_text(audit: FareRuleFareAudit) -> str | None:
    details = audit.structured_details
    if not details or not details.no_show:
        return None

    no_show = details.no_show
    money = _money(no_show)

    if no_show.status == "not_allowed":
        return "No-show: no permitido según la regla tarifaria."

    if no_show.status in {"allowed", "with_fee"}:
        text = "No-show: permitido según la regla tarifaria"
        if money:
            text += f"; penalidad identificada: {money}"
        elif no_show.status == "with_fee":
            text += "; aplica penalidad, sin importe monetario identificado"
        return text + "."

    return None


def _ticketing_text(audit: FareRuleFareAudit) -> str:
    """Return client-safe ticketing wording without exposing BFM internals."""
    ticketing = audit.ticketing

    if ticketing.status == "included":
        text = (ticketing.text or "").strip()

        # Sabre commonly exposes last_ticket_date as ISO. Keep the operational
        # meaning but present the date in the format normally used by agents.
        text = re.sub(
            r"\b(\d{4})-(\d{2})-(\d{2})\b",
            lambda match: (
                f"{match.group(3)}/{match.group(2)}/{match.group(1)}"
            ),
            text,
        )

        if text:
            return text

    return (
        "Fecha límite de emisión a confirmar; "
        "tarifa sujeta a disponibilidad hasta la emisión."
    )


def build_fare_rule_commercial_summary(
    audit: FareRuleFareAudit,
) -> FareRuleCommercialSummary:
    return FareRuleCommercialSummary(
        baggage=audit.baggage.text,
        changes=_changes_text(audit),
        refunds=_refunds_text(audit),
        no_show=_no_show_text(audit),
        ticketing=_ticketing_text(audit),
    )
