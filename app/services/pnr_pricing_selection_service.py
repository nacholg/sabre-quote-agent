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
    """Select the authoritative Sabre pricing set.

    Selection has two phases:

    1. Identify PQs explicitly marked ACTIVE.
    2. If at least one ACTIVE PQ is explicitly current
       (ItineraryChanged=false), only the current ACTIVE set is authoritative.

    This preserves fail-closed behaviour before repricing: when every ACTIVE
    PQ is stale (true) or currentness is unknown (None), those ACTIVE PQs stay
    selected so downstream ticketing checks continue to block.

    Once Sabre contains a fresh ACTIVE PQ, stale/unknown ACTIVE history is
    excluded from passenger coverage, purchase deadline and Ticket Candidate.
    Non-ACTIVE PQs always remain normalized/history only.
    """

    quotes = list(snapshot.price_quotes)
    active = [
        quote
        for quote in quotes
        if _status(quote.status) == "ACTIVE"
    ]
    current_active = [
        quote
        for quote in active
        if quote.itinerary_changed is False
    ]

    # A positively-current ACTIVE set supersedes stale/unknown ACTIVE history.
    candidates = current_active if current_active else active

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

        if current_active:
            excluded_active = len(active) - len(current_active)
            message = (
                "Se seleccionan únicamente las PQ ACTIVE explícitamente "
                "vigentes para el itinerario "
                "(ItineraryChanged=false)."
            )
            if excluded_active:
                message += (
                    f" {excluded_active} PQ ACTIVE stale/no verificable "
                    "queda como histórico y fuera del ticketing candidate."
                )
            non_active = len(quotes) - len(active)
            if non_active:
                message += (
                    f" {non_active} PQ no ACTIVE también queda excluida."
                )
        else:
            message = (
                "Se seleccionan las PQ cuyo status Sabre es ACTIVE. "
                "Todavía no existe una PQ ACTIVE explícitamente vigente "
                "para el itinerario; los checks posteriores deben resolver "
                "ITIN CHG de forma fail-closed."
            )
            non_active = len(quotes) - len(active)
            if non_active:
                message += (
                    f" {non_active} PQ no ACTIVE queda fuera "
                    "de la comparación."
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
