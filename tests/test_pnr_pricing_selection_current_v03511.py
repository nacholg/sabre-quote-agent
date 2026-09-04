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


def _pq(
    record: str,
    *,
    status: str | None = "ACTIVE",
    itinerary_changed: bool | None = None,
    amount: str = "781.33",
) -> PnrPriceQuote:
    return PnrPriceQuote(
        record_number=record,
        status=status,
        itinerary_changed=itinerary_changed,
        total_amount=Decimal(amount),
        total_currency="USD",
        validating_carrier="AA",
    )


def test_stale_active_remains_selected_until_clean_reprice_exists() -> None:
    selection = select_pnr_pricing(
        _snapshot([
            _pq("1", itinerary_changed=True),
        ])
    )

    assert selection.status == PnrPricingSelectionStatus.SELECTED
    assert selection.candidate_record_numbers == ["1"]
    assert selection.candidates[0].itinerary_changed is True


def test_unknown_active_remains_selected_fail_closed_without_clean_pq() -> None:
    selection = select_pnr_pricing(
        _snapshot([
            _pq("1", itinerary_changed=None),
        ])
    )

    assert selection.status == PnrPricingSelectionStatus.SELECTED
    assert selection.candidate_record_numbers == ["1"]
    assert selection.candidates[0].itinerary_changed is None


def test_clean_active_supersedes_stale_active_history() -> None:
    selection = select_pnr_pricing(
        _snapshot([
            _pq(
                "1",
                itinerary_changed=True,
                amount="781.33",
            ),
            _pq(
                "2",
                itinerary_changed=False,
                amount="808.13",
            ),
        ])
    )

    assert selection.status == PnrPricingSelectionStatus.SELECTED
    assert selection.candidate_record_numbers == ["2"]
    assert selection.candidate_quote_count == 1
    assert selection.total_quote_count == 2
    assert selection.excluded_quote_count == 1
    assert selection.candidates[0].total_amount == Decimal("808.13")
    assert "histórico" in (selection.message or "")


def test_clean_active_set_can_contain_multiple_pqs() -> None:
    selection = select_pnr_pricing(
        _snapshot([
            _pq("1", itinerary_changed=True, amount="999.00"),
            _pq("2", itinerary_changed=False, amount="500.00"),
            _pq("3", itinerary_changed=False, amount="308.13"),
            _pq(
                "4",
                status="HISTORICAL",
                itinerary_changed=False,
                amount="111.00",
            ),
        ])
    )

    assert selection.candidate_record_numbers == ["2", "3"]
    assert selection.candidate_quote_count == 2
    assert selection.excluded_quote_count == 2


def test_clean_active_also_supersedes_unknown_active() -> None:
    selection = select_pnr_pricing(
        _snapshot([
            _pq("1", itinerary_changed=None, amount="781.33"),
            _pq("2", itinerary_changed=False, amount="808.13"),
        ])
    )

    assert selection.candidate_record_numbers == ["2"]
    assert selection.candidates[0].itinerary_changed is False
