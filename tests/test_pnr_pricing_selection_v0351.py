from decimal import Decimal

from app.models.pnr_workspace import (
    PnrPriceQuote,
    PnrPricingSelectionStatus,
    PnrSnapshot,
)
from app.services.pnr_pricing_selection_service import select_pnr_pricing


def _snapshot(quotes: list[PnrPriceQuote]) -> PnrSnapshot:
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        price_quotes=quotes,
    )


def _pq(record: str, status: str | None, amount: str) -> PnrPriceQuote:
    return PnrPriceQuote(
        record_number=record,
        status=status,
        total_amount=Decimal(amount),
        total_currency="USD",
        validating_carrier="AA",
    )


def test_no_pq_returns_missing_selection() -> None:
    selection = select_pnr_pricing(_snapshot([]))
    assert selection.status == PnrPricingSelectionStatus.MISSING
    assert selection.candidates == []
    assert selection.total_quote_count == 0
    assert selection.candidate_quote_count == 0
    assert selection.excluded_quote_count == 0


def test_active_status_is_case_insensitive() -> None:
    selection = select_pnr_pricing(_snapshot([_pq("1", "active", "781.33")]))
    assert selection.status == PnrPricingSelectionStatus.SELECTED
    assert selection.candidate_record_numbers == ["1"]
    assert selection.candidate_quote_count == 1
    assert selection.excluded_quote_count == 0


def test_multiple_active_quotes_are_a_candidate_set_not_ambiguity() -> None:
    selection = select_pnr_pricing(
        _snapshot([
            _pq("1", "ACTIVE", "400.00"),
            _pq("2", "ACTIVE", "381.33"),
            _pq("3", "HISTORICAL", "999.99"),
            _pq("4", None, "888.88"),
        ])
    )
    assert selection.status == PnrPricingSelectionStatus.SELECTED
    assert selection.candidate_record_numbers == ["1", "2"]
    assert selection.candidate_quote_count == 2
    assert selection.total_quote_count == 4
    assert selection.excluded_quote_count == 2


def test_quotes_without_explicit_active_status_are_not_selected() -> None:
    selection = select_pnr_pricing(
        _snapshot([
            _pq("1", "HISTORICAL", "781.33"),
            _pq("2", None, "781.33"),
        ])
    )
    assert selection.status == PnrPricingSelectionStatus.NO_ACTIVE
    assert selection.candidates == []
    assert selection.candidate_record_numbers == []
    assert selection.excluded_quote_count == 2
