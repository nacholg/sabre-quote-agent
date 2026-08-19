from __future__ import annotations

from app.models.api import (
    FareRuleAuditResponse,
    FareRuleDatum,
    FareRuleFareAudit,
    FareRuleOptionAudit,
    StoredQuoteRecord,
)
from app.models.itinerary import FareOption, ItineraryOption
from app.sabre.air_rules import AirRulesParsedResponse
from app.services.air_rules_audit_enrichment import enrich_fare_audit_with_air_rules
from app.services.quote_renderer import _brand_feature_status, _fare_baggage_line, _select_commercial_fares


def _status_from_brand(value: str | None, *, kind: str) -> FareRuleDatum:
    if value == "incluido":
        return FareRuleDatum(
            status="included" if kind != "refund" else "allowed",
            source="brand_feature",
            confidence="high",
            text=(
                "Incluido según atributo branded de Sabre."
                if kind != "refund"
                else "Devolución permitida según atributo branded de Sabre."
            ),
        )
    if value == "con cargo":
        return FareRuleDatum(
            status="with_fee",
            source="brand_feature",
            confidence="high",
            text=(
                "Disponible con cargo según atributo branded de Sabre."
                if kind != "refund"
                else "Devolución con cargo según atributo branded de Sabre."
            ),
        )
    if value == "no incluido":
        return FareRuleDatum(
            status="not_allowed",
            source="brand_feature",
            confidence="high",
            text=(
                "No permitido/no ofrecido según atributo branded de Sabre."
            ),
        )
    return FareRuleDatum(
        status="unknown",
        source="not_provided",
        confidence="unknown",
        text="BFM no informó una regla explícita para este concepto.",
    )


def audit_fare(fare: FareOption) -> FareRuleFareAudit:
    baggage_text = _fare_baggage_line(fare)
    baggage_known = bool(fare.baggage) or fare.baggage_pieces is not None
    baggage = FareRuleDatum(
        status=(
            "included"
            if baggage_known and fare.baggage_pieces not in {0, None}
            else "not_allowed"
            if baggage_known and fare.baggage_pieces == 0
            else "unknown"
        ),
        source="baggage" if baggage_known else "not_provided",
        confidence="high" if baggage_known else "unknown",
        text=baggage_text,
    )

    changes = _status_from_brand(
        _brand_feature_status(
            fare,
            ("CHANGE BEFORE DEPARTURE", "CHANGE AFTER DEPARTURE"),
        ),
        kind="change",
    )

    explicit_refund = _status_from_brand(
        _brand_feature_status(
            fare,
            ("REFUND BEFORE DEPARTURE", "REFUND AFTER DEPARTURE"),
        ),
        kind="refund",
    )
    if explicit_refund.status != "unknown":
        refunds = explicit_refund
    elif fare.non_refundable is True:
        refunds = FareRuleDatum(
            status="not_allowed",
            source="fare_flag",
            confidence="high",
            text="Sabre marcó la tarifa como no reembolsable.",
        )
    elif fare.non_refundable is False:
        refunds = FareRuleDatum(
            status="unknown",
            source="fare_flag",
            confidence="medium",
            text=(
                "Sabre no marcó la tarifa como no reembolsable, pero BFM no informó "
                "una regla explícita de devolución. Confirmar fare rules."
            ),
        )
    else:
        refunds = FareRuleDatum(
            status="unknown",
            source="not_provided",
            confidence="unknown",
            text="BFM no informó una regla explícita de devolución.",
        )

    if fare.last_ticket_date:
        ticketing = FareRuleDatum(
            status="included",
            source="ticketing",
            confidence="high",
            text=f"Emitir hasta el {fare.last_ticket_date} o antes si cambia la disponibilidad.",
        )
    else:
        ticketing = FareRuleDatum(
            status="unknown",
            source="not_provided",
            confidence="unknown",
            text="BFM no informó fecha límite de emisión para este producto.",
        )

    return FareRuleFareAudit(
        cabin=fare.cabin,
        brand_name=fare.brand_name,
        brand_code=fare.brand_code,
        currency=fare.currency,
        price_per_passenger=fare.price_per_passenger,
        baggage=baggage,
        changes=changes,
        refunds=refunds,
        ticketing=ticketing,
    )


def commercial_fares(option: ItineraryOption) -> list[FareOption]:
    selected: list[FareOption] = []
    for currency in ("USD", "ARS"):
        selected.extend(
            _select_commercial_fares(
                (option.fare_options_by_currency or {}).get(currency) or []
            )
        )
    if selected:
        return selected
    fares = list((option.fares_by_currency or {}).values())
    return fares or [option.fare]


def audit_stored_quote(
    record: StoredQuoteRecord,
    *,
    selected_only: bool = True,
    air_rules_by_fare_basis: dict[str, AirRulesParsedResponse] | None = None,
) -> FareRuleAuditResponse:
    raw_options = record.quote_response.get("options") or []
    if selected_only and record.selected_ranks:
        wanted = set(record.selected_ranks)
        raw_options = [item for item in raw_options if int(item.get("rank", 0)) in wanted]

    options: list[FareRuleOptionAudit] = []
    requires_lookup = False
    for item in raw_options:
        option = ItineraryOption.model_validate(item["itinerary"])
        audited_fares: list[FareRuleFareAudit] = []
        for fare in commercial_fares(option):
            audit = audit_fare(fare)
            if air_rules_by_fare_basis:
                for fare_basis in fare.fare_basis_codes:
                    parsed = air_rules_by_fare_basis.get(fare_basis)
                    if parsed is not None:
                        audit = enrich_fare_audit_with_air_rules(audit, parsed)
                        break
            audited_fares.append(audit)
        if any(
            audit.changes.status == "unknown" or audit.refunds.status == "unknown"
            for audit in audited_fares
        ):
            requires_lookup = True
        options.append(
            FareRuleOptionAudit(
                rank=int(item["rank"]),
                fares=audited_fares,
            )
        )

    if requires_lookup:
        lookup_status = "pending_authentication"
    elif air_rules_by_fare_basis:
        lookup_status = "resolved"
    else:
        lookup_status = "not_needed"

    return FareRuleAuditResponse(
        quote_id=record.quote_id,
        selected_only=selected_only,
        options=options,
        requires_external_rule_lookup=requires_lookup,
        external_rule_lookup_status=lookup_status,
    )


def reliable_commercial_lines(fare: FareOption) -> list[str]:
    audit = audit_fare(fare)
    lines: list[str] = []

    if audit.changes.status == "included":
        lines.append("Cambios: permitidos sin cargo según atributo branded.")
    elif audit.changes.status == "with_fee":
        lines.append("Cambios: permitidos con cargo según atributo branded.")
    elif audit.changes.status == "not_allowed":
        lines.append("Cambios: no permitidos según atributo branded.")
    else:
        lines.append("Cambios: confirmar reglas tarifarias.")

    if audit.refunds.status == "allowed":
        lines.append("Devoluciones: permitidas según atributo branded.")
    elif audit.refunds.status == "with_fee":
        lines.append("Devoluciones: permitidas con cargo según atributo branded.")
    elif audit.refunds.status == "not_allowed":
        lines.append("Devoluciones: no permitidas.")
    else:
        lines.append("Devoluciones: confirmar reglas tarifarias.")

    return lines
