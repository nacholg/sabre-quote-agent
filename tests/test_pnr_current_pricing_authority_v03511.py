from decimal import Decimal
from types import SimpleNamespace

from app.models.pnr_workspace import (
    PnrPricingAuthority,
    PnrPricingCoverage,
    PnrPricingCoverageStatus,
    PnrPricingPassengerBinding,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrPriceQuote,
    PnrSnapshot,
    PnrTicketCandidateStatus,
)
from app.services.pnr_pricing_authority_service import (
    resolve_pnr_pricing_authority,
)
from app.services.pnr_ticket_candidate_service import (
    build_pnr_ticket_candidate,
)


def _fare():
    return SimpleNamespace(
        currency="USD",
        validating_carrier="AA",
        total_price=Decimal("781.33"),
        brand_code="MAINFL",
    )


def _quote(record: str = "2") -> PnrPriceQuote:
    return PnrPriceQuote(
        record_number=record,
        status="ACTIVE",
        passenger_type="ADT",
        passenger_quantity=1,
        passenger_name_numbers=["01.01"],
        total_currency="USD",
        validating_carrier="AA",
        total_amount=Decimal("808.13"),
        fare_basis_codes=["SLN7AHM5/L040"],
        purchase_deadline_raw="LAST DAY TO PURCHASE 05SEP/2359",
        itinerary_changed=False,
    )


def _selection(record: str = "2") -> PnrPricingSelection:
    return PnrPricingSelection(
        status=PnrPricingSelectionStatus.SELECTED,
        candidates=[_quote(record)],
        total_quote_count=2,
        candidate_quote_count=1,
        excluded_quote_count=1,
        candidate_record_numbers=[record],
    )


def _authority(record: str = "2") -> PnrPricingAuthority:
    return PnrPricingAuthority(
        pricing_authority_id=1,
        booking_id="B-20260831-65FBA856",
        confirmation_id="OVFOTM",
        price_quote_record_numbers=[record],
        brand_code="MAINFL",
        brand_name="MAIN CABIN FLEXIBLE",
        original_total=Decimal("781.33"),
        current_total=Decimal("808.13"),
        currency="USD",
        price_difference=Decimal("26.80"),
        validating_carrier="AA",
        fare_basis_codes=["SLN7AHM5/L040"],
        purchase_deadline_raw="LAST DAY TO PURCHASE 05SEP/2359",
        provider="sabre_brand_pq_store",
        verified_at="2026-09-04T17:00:00+00:00",
    )


def _coverage() -> PnrPricingCoverage:
    return PnrPricingCoverage(
        status=PnrPricingCoverageStatus.EXACT,
        passenger_count=1,
        covered_passenger_count=1,
        bindings=[
            PnrPricingPassengerBinding(
                name_number="01.01",
                passenger_type="ADT",
                candidate_record_numbers=["2"],
            )
        ],
    )


def test_real_cert_refreshed_pq_becomes_current_authority() -> None:
    result = resolve_pnr_pricing_authority(
        booking_id="B-20260831-65FBA856",
        confirmation_id="OVFOTM",
        fare=_fare(),
        selection=_selection(),
        authority=_authority(),
    )
    assert result.current is True
    assert result.blockers == ()
    assert result.expected_total == Decimal("808.13")


def test_stale_authority_record_fails_closed() -> None:
    result = resolve_pnr_pricing_authority(
        booking_id="B-20260831-65FBA856",
        confirmation_id="OVFOTM",
        fare=_fare(),
        selection=_selection(record="3"),
        authority=_authority(record="2"),
    )
    assert result.current is False
    assert "AUTHORITY_PQ_RECORD_MISMATCH" in result.blockers
    assert result.expected_total == Decimal("781.33")


def test_ticket_candidate_accepts_verified_refreshed_total() -> None:
    selection = _selection()
    result = resolve_pnr_pricing_authority(
        booking_id="B-20260831-65FBA856",
        confirmation_id="OVFOTM",
        fare=_fare(),
        selection=selection,
        authority=_authority(),
    )
    candidate = build_pnr_ticket_candidate(
        snapshot=PnrSnapshot(
            confirmation_id="OVFOTM",
            application_status="Complete",
        ),
        fare=_fare(),
        selection=selection,
        coverage=_coverage(),
        expected_total_override=result.expected_total,
        expected_currency_override=result.expected_currency,
        expected_validating_carrier_override=(
            result.expected_validating_carrier
        ),
    )
    assert candidate.status == PnrTicketCandidateStatus.READY
    assert candidate.total_amount == Decimal("808.13")
    assert candidate.blockers == []
