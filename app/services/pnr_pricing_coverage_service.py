from __future__ import annotations

import re
from collections import defaultdict

from app.models.pnr_workspace import (
    PnrPricingCoverage,
    PnrPricingCoverageStatus,
    PnrPricingPassengerBinding,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrSnapshot,
)


def _name_number(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(".")
    if all(part.isdigit() for part in parts):
        return ".".join(str(int(part)) for part in parts)
    return text.upper()


def _pax_kind(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return None
    if normalized in {"ADT", "INF"}:
        return normalized
    if normalized in {"CHILD", "CNN", "CHD"} or re.fullmatch(r"C\d{2}", normalized):
        return "CHILD"
    return normalized


def assess_pnr_pricing_coverage(
    snapshot: PnrSnapshot,
    selection: PnrPricingSelection,
) -> PnrPricingCoverage:
    """Assess explicit ACTIVE-PQ passenger associations conservatively."""

    passengers: dict[str, str | None] = {}
    originals: dict[str, str] = {}
    for passenger in snapshot.passengers:
        normalized = _name_number(passenger.name_number)
        if normalized is None or normalized in passengers:
            return PnrPricingCoverage(
                status=PnrPricingCoverageStatus.UNKNOWN,
                passenger_count=len(snapshot.passengers),
                message="Sabre no devolvió NameNumber inequívoco para todos los pasajeros.",
            )
        passengers[normalized] = _pax_kind(passenger.passenger_type)
        originals[normalized] = passenger.name_number

    if selection.status != PnrPricingSelectionStatus.SELECTED or not selection.candidates:
        return PnrPricingCoverage(
            status=PnrPricingCoverageStatus.UNKNOWN,
            passenger_count=len(snapshot.passengers),
            message="No hay un conjunto de PQ ACTIVE seleccionado para evaluar cobertura de pasajeros.",
        )

    coverage_records: dict[str, list[str]] = defaultdict(list)
    coverage_types: dict[str, list[str | None]] = defaultdict(list)
    unknown_names: set[str] = set()
    quantity_mismatches: list[str] = []
    unassociated_records: list[str] = []

    for index, quote in enumerate(selection.candidates, start=1):
        record = str(quote.record_number or index)
        associated = [
            value
            for value in (_name_number(name) for name in quote.passenger_name_numbers)
            if value
        ]
        unique_associated = list(dict.fromkeys(associated))
        if not unique_associated:
            unassociated_records.append(record)
            continue
        if quote.passenger_quantity is not None and quote.passenger_quantity != len(unique_associated):
            quantity_mismatches.append(record)

        quote_type = _pax_kind(quote.passenger_type)
        for name in unique_associated:
            if name not in passengers:
                unknown_names.add(name)
                continue
            coverage_records[name].append(record)
            coverage_types[name].append(quote_type)

    uncovered = [originals[name] for name in passengers if not coverage_records.get(name)]
    duplicates = [originals[name] for name in passengers if len(coverage_records.get(name, [])) > 1]
    type_mismatches: list[str] = []
    for name, actual_type in passengers.items():
        candidate_types = coverage_types.get(name, [])
        if not candidate_types:
            continue
        if actual_type is None or any(candidate is None or candidate != actual_type for candidate in candidate_types):
            type_mismatches.append(originals[name])

    bindings = [
        PnrPricingPassengerBinding(
            name_number=originals[name],
            passenger_type=passengers[name],
            candidate_record_numbers=list(coverage_records.get(name, [])),
        )
        for name in passengers
    ]
    covered_count = sum(1 for name in passengers if coverage_records.get(name))

    if unassociated_records:
        status = PnrPricingCoverageStatus.UNKNOWN
        message = "Una o más PQ ACTIVE no tienen asociación PassengerData/NameNumber; no se infiere cobertura por PTC o cantidad."
    elif unknown_names or duplicates or type_mismatches or quantity_mismatches:
        status = PnrPricingCoverageStatus.CONFLICT
        message = "Las asociaciones de PQ ACTIVE tienen conflictos que requieren revisión."
    elif uncovered:
        status = PnrPricingCoverageStatus.INCOMPLETE
        message = "Hay pasajeros del PNR sin cobertura de una PQ ACTIVE."
    else:
        status = PnrPricingCoverageStatus.EXACT
        message = "Cada pasajero del PNR está asociado exactamente una vez a pricing ACTIVE."

    return PnrPricingCoverage(
        status=status,
        passenger_count=len(passengers),
        covered_passenger_count=covered_count,
        bindings=bindings,
        uncovered_name_numbers=uncovered,
        duplicate_name_numbers=duplicates,
        unknown_name_numbers=sorted(unknown_names),
        type_mismatch_name_numbers=type_mismatches,
        quantity_mismatch_record_numbers=quantity_mismatches,
        unassociated_record_numbers=unassociated_records,
        message=message,
    )
