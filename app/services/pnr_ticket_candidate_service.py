from __future__ import annotations

from decimal import Decimal

from app.models.pnr_workspace import (
    PnrPricingCoverage,
    PnrPricingCoverageStatus,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrSnapshot,
    PnrTicketCandidate,
    PnrTicketCandidatePassenger,
    PnrTicketCandidateStatus,
)


def _upper(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


def build_pnr_ticket_candidate(
    *,
    snapshot: PnrSnapshot,
    fare,
    selection: PnrPricingSelection,
    coverage: PnrPricingCoverage,
) -> PnrTicketCandidate:
    """Build a read-only, explicit future ticketing candidate.

    This function performs no Sabre call and no mutation. READY means the
    currently-read PNR provides an unambiguous ACTIVE-PQ set, explicit
    passenger bindings, one currency, one validating carrier, and a complete
    total matching the frozen accepted Booking fare.
    """

    blockers: list[str] = []
    locator = str(snapshot.confirmation_id or "").strip().upper()
    if not locator:
        blockers.append("MISSING_CONFIRMATION_ID")

    selected = (
        selection.status == PnrPricingSelectionStatus.SELECTED
        and bool(selection.candidates)
    )
    if not selected:
        blockers.append("NO_ACTIVE_PRICING")

    if selected:
        itinerary_changed_values = [
            quote.itinerary_changed
            for quote in selection.candidates
        ]
        if any(value is True for value in itinerary_changed_values):
            blockers.append("PQ_ITINERARY_CHANGED")
        elif any(value is None for value in itinerary_changed_values):
            blockers.append("PQ_ITINERARY_CHANGE_UNKNOWN")

    if coverage.status != PnrPricingCoverageStatus.EXACT:
        blockers.append("PRICING_COVERAGE_NOT_EXACT")

    record_numbers = [
        str(quote.record_number or "").strip()
        for quote in selection.candidates
    ]
    if any(not record for record in record_numbers):
        blockers.append("MISSING_PQ_RECORD_NUMBER")
    non_empty_records = [record for record in record_numbers if record]
    if len(set(non_empty_records)) != len(non_empty_records):
        blockers.append("DUPLICATE_PQ_RECORD_NUMBER")
    record_set = set(non_empty_records)

    expected_currency = _upper(getattr(fare, "currency", None))
    currencies = [
        _upper(quote.total_currency)
        for quote in selection.candidates
    ]
    if expected_currency is None:
        blockers.append("BOOKING_CURRENCY_UNKNOWN")
    if selected and any(value is None for value in currencies):
        blockers.append("MISSING_PQ_CURRENCY")
    currency_set = {value for value in currencies if value}
    if len(currency_set) > 1:
        blockers.append("MULTIPLE_PQ_CURRENCIES")
    candidate_currency = (
        next(iter(currency_set))
        if len(currency_set) == 1
        else None
    )
    if (
        expected_currency is not None
        and candidate_currency is not None
        and candidate_currency != expected_currency
    ):
        blockers.append("CURRENCY_MISMATCH")

    expected_carrier = _upper(getattr(fare, "validating_carrier", None))
    carriers = [
        _upper(quote.validating_carrier)
        for quote in selection.candidates
    ]
    if expected_carrier is None:
        blockers.append("BOOKING_VALIDATING_CARRIER_UNKNOWN")
    if selected and any(value is None for value in carriers):
        blockers.append("MISSING_VALIDATING_CARRIER")
    carrier_set = {value for value in carriers if value}
    if len(carrier_set) > 1:
        blockers.append("MULTIPLE_VALIDATING_CARRIERS")
    candidate_carrier = (
        next(iter(carrier_set))
        if len(carrier_set) == 1
        else None
    )
    if (
        expected_carrier is not None
        and candidate_carrier is not None
        and candidate_carrier != expected_carrier
    ):
        blockers.append("VALIDATING_CARRIER_MISMATCH")

    expected_total = getattr(fare, "total_price", None)
    totals = [quote.total_amount for quote in selection.candidates]
    candidate_total: Decimal | None = None
    if expected_total is None:
        blockers.append("BOOKING_TOTAL_UNKNOWN")
    if selected and any(value is None for value in totals):
        blockers.append("MISSING_PQ_TOTAL")
    elif selected:
        candidate_total = sum(
            (value for value in totals if value is not None),
            Decimal("0"),
        )
        if (
            expected_total is not None
            and candidate_total != expected_total
        ):
            blockers.append("TOTAL_MISMATCH")

    passengers: list[PnrTicketCandidatePassenger] = []
    if coverage.status == PnrPricingCoverageStatus.EXACT:
        for binding in coverage.bindings:
            if len(binding.candidate_record_numbers) != 1:
                blockers.append("PASSENGER_BINDING_NOT_EXACT")
                continue
            record = str(binding.candidate_record_numbers[0] or "").strip()
            if not record or record not in record_set:
                blockers.append("PASSENGER_BINDING_UNKNOWN_PQ")
                continue
            passenger_type = _upper(binding.passenger_type)
            if passenger_type is None:
                blockers.append("PASSENGER_TYPE_UNKNOWN")
                continue
            passengers.append(
                PnrTicketCandidatePassenger(
                    name_number=binding.name_number,
                    passenger_type=passenger_type,
                    price_quote_record_number=record,
                )
            )

        if len(passengers) != coverage.passenger_count:
            blockers.append("PASSENGER_BINDING_COUNT_MISMATCH")

    blockers = list(dict.fromkeys(blockers))
    ready = not blockers
    return PnrTicketCandidate(
        status=(
            PnrTicketCandidateStatus.READY
            if ready
            else PnrTicketCandidateStatus.BLOCKED
        ),
        confirmation_id=locator,
        validating_carrier=candidate_carrier,
        currency=candidate_currency,
        total_amount=candidate_total,
        price_quote_record_numbers=non_empty_records,
        passengers=passengers,
        blockers=blockers,
        message=(
            "Ticket candidate inequívoco construido desde el PNR actual."
            if ready
            else (
                "El PNR actual todavía no permite construir un ticket "
                "candidate inequívoco."
            )
        ),
    )
