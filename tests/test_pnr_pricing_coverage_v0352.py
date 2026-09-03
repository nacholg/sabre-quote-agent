from app.models.pnr_workspace import (
    PnrPassenger,
    PnrPriceQuote,
    PnrPricingCoverageStatus,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrSnapshot,
)
from app.services.pnr_pricing_coverage_service import assess_pnr_pricing_coverage


def _pq(record: str, *, names: list[str], ptc: str = "ADT", quantity: int | None = None) -> PnrPriceQuote:
    return PnrPriceQuote(
        record_number=record,
        status="ACTIVE",
        passenger_type=ptc,
        passenger_quantity=len(names) if quantity is None else quantity,
        passenger_name_numbers=names,
    )


def _selection(*quotes: PnrPriceQuote) -> PnrPricingSelection:
    return PnrPricingSelection(
        status=PnrPricingSelectionStatus.SELECTED,
        candidates=list(quotes),
        total_quote_count=len(quotes),
        candidate_quote_count=len(quotes),
        excluded_quote_count=0,
        candidate_record_numbers=[str(q.record_number) for q in quotes],
    )


def _snapshot(passengers: list[tuple[str, str]]) -> PnrSnapshot:
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        passengers=[PnrPassenger(name_number=n, passenger_type=p) for n, p in passengers],
    )


def test_real_cert_shape_has_exact_coverage() -> None:
    coverage = assess_pnr_pricing_coverage(
        _snapshot([("01.01", "ADT")]),
        _selection(_pq("1", names=["01.01"])),
    )
    assert coverage.status == PnrPricingCoverageStatus.EXACT
    assert coverage.covered_passenger_count == 1
    assert coverage.bindings[0].candidate_record_numbers == ["1"]


def test_name_number_zero_padding_is_canonicalized() -> None:
    coverage = assess_pnr_pricing_coverage(
        _snapshot([("01.01", "ADT")]),
        _selection(_pq("1", names=["1.1"])),
    )
    assert coverage.status == PnrPricingCoverageStatus.EXACT


def test_uncovered_passenger_is_incomplete() -> None:
    coverage = assess_pnr_pricing_coverage(
        _snapshot([("01.01", "ADT"), ("02.01", "ADT")]),
        _selection(_pq("1", names=["01.01"])),
    )
    assert coverage.status == PnrPricingCoverageStatus.INCOMPLETE
    assert coverage.uncovered_name_numbers == ["02.01"]


def test_duplicate_active_pq_coverage_is_conflict() -> None:
    coverage = assess_pnr_pricing_coverage(
        _snapshot([("01.01", "ADT")]),
        _selection(_pq("1", names=["01.01"]), _pq("2", names=["01.01"])),
    )
    assert coverage.status == PnrPricingCoverageStatus.CONFLICT
    assert coverage.duplicate_name_numbers == ["01.01"]


def test_unknown_name_number_is_conflict() -> None:
    coverage = assess_pnr_pricing_coverage(
        _snapshot([("01.01", "ADT")]),
        _selection(_pq("1", names=["09.09"])),
    )
    assert coverage.status == PnrPricingCoverageStatus.CONFLICT
    assert coverage.unknown_name_numbers == ["9.9"]


def test_ptc_mismatch_is_conflict() -> None:
    coverage = assess_pnr_pricing_coverage(
        _snapshot([("01.01", "ADT")]),
        _selection(_pq("1", names=["01.01"], ptc="INF")),
    )
    assert coverage.status == PnrPricingCoverageStatus.CONFLICT
    assert coverage.type_mismatch_name_numbers == ["01.01"]


def test_quantity_mismatch_is_conflict() -> None:
    coverage = assess_pnr_pricing_coverage(
        _snapshot([("01.01", "ADT")]),
        _selection(_pq("1", names=["01.01"], quantity=2)),
    )
    assert coverage.status == PnrPricingCoverageStatus.CONFLICT
    assert coverage.quantity_mismatch_record_numbers == ["1"]


def test_missing_explicit_name_association_is_unknown() -> None:
    coverage = assess_pnr_pricing_coverage(
        _snapshot([("01.01", "ADT")]),
        _selection(_pq("1", names=[], quantity=1)),
    )
    assert coverage.status == PnrPricingCoverageStatus.UNKNOWN
    assert coverage.unassociated_record_numbers == ["1"]
