from decimal import Decimal

from app.models.commercial_quote import CommercialFare
from app.models.pnr_workspace import (
    PnrPriceQuote,
    PnrPricingCoverage,
    PnrPricingCoverageStatus,
    PnrPricingPassengerBinding,
    PnrSnapshot,
    PnrTicketCandidateStatus,
)
from app.services.pnr_pricing_coverage_service import (
    assess_pnr_pricing_coverage,
)
from app.services.pnr_pricing_selection_service import select_pnr_pricing
from app.services.pnr_ticket_candidate_service import (
    build_pnr_ticket_candidate,
)


def test_ticket_candidate_uses_clean_active_pq_not_stale_active_history() -> None:
    snapshot = PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        passengers=[],
        price_quotes=[
            PnrPriceQuote(
                record_number="1",
                status="ACTIVE",
                itinerary_changed=True,
                passenger_type="ADT",
                passenger_quantity=1,
                passenger_name_numbers=["01.01"],
                total_amount=Decimal("781.33"),
                total_currency="USD",
                validating_carrier="AA",
            ),
            PnrPriceQuote(
                record_number="2",
                status="ACTIVE",
                itinerary_changed=False,
                passenger_type="ADT",
                passenger_quantity=1,
                passenger_name_numbers=["01.01"],
                total_amount=Decimal("808.13"),
                total_currency="USD",
                validating_carrier="AA",
            ),
        ],
    )

    # Coverage is built against the selected current set. Use one normalized
    # passenger because production coverage requires the PNR passenger list.
    from app.models.pnr_workspace import PnrPassenger

    snapshot.passengers = [
        PnrPassenger(
            name_number="01.01",
            passenger_type="ADT",
        )
    ]

    selection = select_pnr_pricing(snapshot)
    coverage = assess_pnr_pricing_coverage(snapshot, selection)

    # The new local fare authority will move to 808.13 in the next v0.35.11
    # slice; model it here to prove stale PQ1 cannot contaminate candidate PQ2.
    fare = CommercialFare(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal("808.13"),
        total_price=Decimal("808.13"),
        validating_carrier="AA",
    )

    candidate = build_pnr_ticket_candidate(
        snapshot=snapshot,
        fare=fare,
        selection=selection,
        coverage=coverage,
    )

    assert selection.candidate_record_numbers == ["2"]
    assert coverage.status == PnrPricingCoverageStatus.EXACT
    assert candidate.status == PnrTicketCandidateStatus.READY
    assert candidate.price_quote_record_numbers == ["2"]
    assert candidate.total_amount == Decimal("808.13")
