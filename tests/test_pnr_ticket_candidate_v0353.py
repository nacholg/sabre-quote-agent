from decimal import Decimal
from types import SimpleNamespace

from app.models.pnr_workspace import (
    PnrPricingCoverage,
    PnrPricingCoverageStatus,
    PnrPricingPassengerBinding,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrPriceQuote,
    PnrSnapshot,
    PnrTicketCandidateStatus,
)
from app.services.pnr_ticket_candidate_service import (
    build_pnr_ticket_candidate,
)


def _fare(
    *,
    currency: str = "USD",
    carrier: str = "AA",
    total: str = "781.33",
):
    return SimpleNamespace(
        currency=currency,
        validating_carrier=carrier,
        total_price=Decimal(total),
    )


def _quote(
    *,
    record: str | None = "1",
    currency: str | None = "USD",
    carrier: str | None = "AA",
    total: str | None = "781.33",
    itinerary_changed: bool | None = False,
) -> PnrPriceQuote:
    return PnrPriceQuote(
        record_number=record,
        status="ACTIVE",
        passenger_type="ADT",
        passenger_quantity=1,
        passenger_name_numbers=["01.01"],
        total_currency=currency,
        validating_carrier=carrier,
        total_amount=(Decimal(total) if total is not None else None),
        itinerary_changed=itinerary_changed,
    )


def _selection(*quotes: PnrPriceQuote) -> PnrPricingSelection:
    return PnrPricingSelection(
        status=PnrPricingSelectionStatus.SELECTED,
        candidates=list(quotes),
        total_quote_count=len(quotes),
        candidate_quote_count=len(quotes),
        excluded_quote_count=0,
        candidate_record_numbers=[
            str(quote.record_number)
            for quote in quotes
            if quote.record_number
        ],
    )


def _coverage(
    *,
    status: PnrPricingCoverageStatus = PnrPricingCoverageStatus.EXACT,
    record: str = "1",
) -> PnrPricingCoverage:
    return PnrPricingCoverage(
        status=status,
        passenger_count=1,
        covered_passenger_count=(
            1 if status == PnrPricingCoverageStatus.EXACT else 0
        ),
        bindings=[
            PnrPricingPassengerBinding(
                name_number="01.01",
                passenger_type="ADT",
                candidate_record_numbers=[record],
            )
        ],
    )


def _snapshot() -> PnrSnapshot:
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
    )


def test_real_cert_shape_builds_ready_candidate() -> None:
    candidate = build_pnr_ticket_candidate(
        snapshot=_snapshot(),
        fare=_fare(),
        selection=_selection(_quote()),
        coverage=_coverage(),
    )

    assert candidate.status == PnrTicketCandidateStatus.READY
    assert candidate.confirmation_id == "OVFOTM"
    assert candidate.price_quote_record_numbers == ["1"]
    assert candidate.validating_carrier == "AA"
    assert candidate.currency == "USD"
    assert candidate.total_amount == Decimal("781.33")
    assert candidate.blockers == []
    assert len(candidate.passengers) == 1
    assert candidate.passengers[0].name_number == "01.01"
    assert candidate.passengers[0].passenger_type == "ADT"
    assert candidate.passengers[0].price_quote_record_number == "1"


def test_missing_real_pq_record_number_blocks_candidate() -> None:
    candidate = build_pnr_ticket_candidate(
        snapshot=_snapshot(),
        fare=_fare(),
        selection=_selection(_quote(record=None)),
        coverage=_coverage(record="1"),
    )

    assert candidate.status == PnrTicketCandidateStatus.BLOCKED
    assert "MISSING_PQ_RECORD_NUMBER" in candidate.blockers
    assert "PASSENGER_BINDING_UNKNOWN_PQ" in candidate.blockers


def test_total_mismatch_blocks_candidate() -> None:
    candidate = build_pnr_ticket_candidate(
        snapshot=_snapshot(),
        fare=_fare(),
        selection=_selection(_quote(total="800.00")),
        coverage=_coverage(),
    )

    assert candidate.status == PnrTicketCandidateStatus.BLOCKED
    assert "TOTAL_MISMATCH" in candidate.blockers


def test_validating_carrier_mismatch_blocks_candidate() -> None:
    candidate = build_pnr_ticket_candidate(
        snapshot=_snapshot(),
        fare=_fare(),
        selection=_selection(_quote(carrier="BA")),
        coverage=_coverage(),
    )

    assert candidate.status == PnrTicketCandidateStatus.BLOCKED
    assert "VALIDATING_CARRIER_MISMATCH" in candidate.blockers


def test_non_exact_coverage_never_builds_ready_candidate() -> None:
    candidate = build_pnr_ticket_candidate(
        snapshot=_snapshot(),
        fare=_fare(),
        selection=_selection(_quote()),
        coverage=_coverage(
            status=PnrPricingCoverageStatus.UNKNOWN
        ),
    )

    assert candidate.status == PnrTicketCandidateStatus.BLOCKED
    assert "PRICING_COVERAGE_NOT_EXACT" in candidate.blockers


def test_two_active_pqs_can_form_one_candidate_set() -> None:
    first = _quote(record="1", total="500.00")
    first.passenger_name_numbers = ["01.01"]
    second = PnrPriceQuote(
        record_number="2",
        status="ACTIVE",
        passenger_type="CHD",
        passenger_quantity=1,
        passenger_name_numbers=["02.01"],
        total_currency="USD",
        validating_carrier="AA",
        total_amount=Decimal("281.33"),
        itinerary_changed=False,
    )
    coverage = PnrPricingCoverage(
        status=PnrPricingCoverageStatus.EXACT,
        passenger_count=2,
        covered_passenger_count=2,
        bindings=[
            PnrPricingPassengerBinding(
                name_number="01.01",
                passenger_type="ADT",
                candidate_record_numbers=["1"],
            ),
            PnrPricingPassengerBinding(
                name_number="02.01",
                passenger_type="CHILD",
                candidate_record_numbers=["2"],
            ),
        ],
    )

    candidate = build_pnr_ticket_candidate(
        snapshot=_snapshot(),
        fare=_fare(total="781.33"),
        selection=_selection(first, second),
        coverage=coverage,
    )

    assert candidate.status == PnrTicketCandidateStatus.READY
    assert candidate.price_quote_record_numbers == ["1", "2"]
    assert candidate.total_amount == Decimal("781.33")
    assert [p.price_quote_record_number for p in candidate.passengers] == [
        "1",
        "2",
    ]


def test_itinerary_changed_blocks_ticket_candidate() -> None:
    candidate = build_pnr_ticket_candidate(
        snapshot=_snapshot(),
        fare=_fare(),
        selection=_selection(
            _quote(itinerary_changed=True)
        ),
        coverage=_coverage(),
    )

    assert candidate.status == PnrTicketCandidateStatus.BLOCKED
    assert candidate.blockers == ["PQ_ITINERARY_CHANGED"]


def test_unknown_itinerary_changed_fails_closed() -> None:
    candidate = build_pnr_ticket_candidate(
        snapshot=_snapshot(),
        fare=_fare(),
        selection=_selection(
            _quote(itinerary_changed=None)
        ),
        coverage=_coverage(),
    )

    assert candidate.status == PnrTicketCandidateStatus.BLOCKED
    assert candidate.blockers == ["PQ_ITINERARY_CHANGE_UNKNOWN"]
