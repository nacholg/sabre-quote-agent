from __future__ import annotations

from app.models.pnr_workspace import (
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrSnapshot,
)


def _status(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


def select_pnr_pricing(snapshot: PnrSnapshot) -> PnrPricingSelection:
    """Select only Sabre PQs explicitly marked ACTIVE.

    v0.35.1 deliberately does not infer that any other status means
    historical, superseded, void, or otherwise unusable. Non-ACTIVE quotes
    remain visible in the normalized snapshot, but are excluded from pricing
    comparisons and from the future ticketing candidate set.
    """

    quotes = list(snapshot.price_quotes)
    candidates = [
        quote
        for quote in quotes
        if _status(quote.status) == "ACTIVE"
    ]
    records = [
        value
        for value in (
            str(quote.record_number or "").strip()
            for quote in candidates
        )
        if value
    ]

    if candidates:
        excluded = len(quotes) - len(candidates)
        message = (
            "Se seleccionan únicamente las PQ cuyo status Sabre es ACTIVE."
        )
        if excluded:
            message += (
                f" {excluded} PQ no ACTIVE queda fuera de la comparación."
            )
        return PnrPricingSelection(
            status=PnrPricingSelectionStatus.SELECTED,
            candidates=candidates,
            total_quote_count=len(quotes),
            candidate_quote_count=len(candidates),
            excluded_quote_count=excluded,
            candidate_record_numbers=records,
            message=message,
        )

    if quotes:
        return PnrPricingSelection(
            status=PnrPricingSelectionStatus.NO_ACTIVE,
            candidates=[],
            total_quote_count=len(quotes),
            candidate_quote_count=0,
            excluded_quote_count=len(quotes),
            candidate_record_numbers=[],
            message=(
                "Sabre devolvió PQs, pero ninguna tiene status ACTIVE. "
                "No se selecciona pricing candidato."
            ),
        )

    return PnrPricingSelection(
        status=PnrPricingSelectionStatus.MISSING,
        candidates=[],
        total_quote_count=0,
        candidate_quote_count=0,
        excluded_quote_count=0,
        candidate_record_numbers=[],
        message="Sabre no devolvió ninguna PQ.",
    )
