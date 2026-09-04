from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

from app.models.pnr_workspace import (
    PnrPricingCoverageStatus,
    PnrPricingSelectionStatus,
    PnrSecureFlightDocsStatus,
    PnrSnapshot,
)
from app.sabre.soap_brand_pq_store import SabreBrandPriceResult
from app.services.pnr_pricing_coverage_service import (
    assess_pnr_pricing_coverage,
)
from app.services.pnr_pricing_selection_service import select_pnr_pricing
from app.services.pnr_secure_flight_docs_service import (
    assess_pnr_secure_flight_docs,
)


class PnrPricingAuthorityBackfillError(RuntimeError):
    """Read-only Sabre evidence is insufficient for local authority backfill."""


@dataclass(frozen=True)
class VerifiedPnrPricingAuthorityBackfill:
    booking_id: str
    confirmation_id: str
    price_quote_record_numbers: list[str]
    brand_code: str
    brand_name: str | None
    original_total: Decimal
    current_total: Decimal
    currency: str
    validating_carrier: str
    fare_basis_codes: list[str]
    purchase_deadline_raw: str
    provider: str = "sabre_brand_pq_backfill_v03511"


def _upper(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


def _deadline_token(value: str | None) -> str | None:
    text = _upper(value)
    if not text:
        return None
    match = re.search(r"(\d{1,2}[A-Z]{3}/\d{4})", text)
    return match.group(1) if match else None


def _quote_fare_basis(quote) -> list[str]:
    values = [
        _upper(value)
        for value in (quote.fare_basis_codes or [])
        if _upper(value)
    ]
    if not values and _upper(quote.fare_basis):
        values = [_upper(quote.fare_basis)]
    return list(dict.fromkeys(value for value in values if value))


def verify_pnr_pricing_authority_backfill(
    *,
    booking_id: str,
    confirmation_id: str,
    fare,
    snapshot: PnrSnapshot,
    requested_brand_code: str,
    expected_total: Decimal,
    expected_record_numbers: list[str],
    preview: SabreBrandPriceResult,
) -> VerifiedPnrPricingAuthorityBackfill:
    """Fail-closed verification for a one-time local pricing-authority backfill.

    Sabre is read only here: fresh TIR + price-by-brand preview without RQ.
    The exact requested brand is authoritative because TIR does not expose a
    reliable BrandID. No brand is inferred from fare basis.
    """

    locator = str(confirmation_id or "").strip().upper()
    if not locator or _upper(snapshot.confirmation_id) != locator:
        raise PnrPricingAuthorityBackfillError(
            "LOCATOR_MISMATCH: fresh TIR no coincide con el PNR esperado."
        )

    original_brand = _upper(getattr(fare, "brand_code", None))
    requested_brand = _upper(requested_brand_code)
    if original_brand is None:
        raise PnrPricingAuthorityBackfillError(
            "ORIGINAL_BRAND_UNKNOWN: Booking no conserva BrandID exacto."
        )
    if requested_brand != original_brand:
        raise PnrPricingAuthorityBackfillError(
            "BRAND_MISMATCH: el backfill sólo permite el mismo BrandID aceptado."
        )

    original_total = getattr(fare, "total_price", None)
    if original_total is None:
        raise PnrPricingAuthorityBackfillError(
            "ORIGINAL_TOTAL_UNKNOWN: Booking no conserva total aceptado."
        )

    currency = _upper(getattr(fare, "currency", None))
    if currency is None or len(currency) != 3:
        raise PnrPricingAuthorityBackfillError(
            "CURRENCY_UNKNOWN: Booking no conserva moneda ISO verificable."
        )

    expected_carrier = _upper(getattr(fare, "validating_carrier", None))
    if expected_carrier is None:
        raise PnrPricingAuthorityBackfillError(
            "VALIDATING_CARRIER_UNKNOWN: no se puede fijar autoridad sin carrier."
        )

    docs = assess_pnr_secure_flight_docs(snapshot)
    if docs.status != PnrSecureFlightDocsStatus.COMPLETE:
        raise PnrPricingAuthorityBackfillError(
            "SECURE_FLIGHT_DOCS_NOT_COMPLETE: DOCS debe estar HK y asociado "
            "a todos los pasajeros en fresh TIR."
        )

    selection = select_pnr_pricing(snapshot)
    if (
        selection.status != PnrPricingSelectionStatus.SELECTED
        or not selection.candidates
    ):
        raise PnrPricingAuthorityBackfillError(
            "CURRENT_PQ_NOT_SELECTED: no hay pricing ACTIVE/current inequívoco."
        )

    expected_records = [
        str(value).strip()
        for value in expected_record_numbers
        if str(value).strip()
    ]
    actual_records = [
        str(quote.record_number or "").strip()
        for quote in selection.candidates
    ]
    if (
        not expected_records
        or any(not value for value in actual_records)
        or actual_records != expected_records
    ):
        raise PnrPricingAuthorityBackfillError(
            "PQ_RECORD_MISMATCH: el PQ current no coincide con el esperado."
        )

    if any(
        quote.itinerary_changed is not False
        for quote in selection.candidates
    ):
        raise PnrPricingAuthorityBackfillError(
            "PQ_NOT_CURRENT: ItineraryChanged debe ser explícitamente false."
        )

    coverage = assess_pnr_pricing_coverage(snapshot, selection)
    if coverage.status != PnrPricingCoverageStatus.EXACT:
        raise PnrPricingAuthorityBackfillError(
            "PRICING_COVERAGE_NOT_EXACT: cobertura de pasajeros no es exacta."
        )

    totals = [quote.total_amount for quote in selection.candidates]
    if any(value is None for value in totals):
        raise PnrPricingAuthorityBackfillError(
            "PQ_TOTAL_UNKNOWN: TIR no devolvió total para todos los PQ current."
        )
    current_total = sum(
        (value for value in totals if value is not None),
        Decimal("0"),
    )
    if current_total != expected_total:
        raise PnrPricingAuthorityBackfillError(
            "PQ_TOTAL_MISMATCH: total current de TIR difiere del esperado."
        )

    currencies = {
        _upper(quote.total_currency)
        for quote in selection.candidates
        if _upper(quote.total_currency)
    }
    if currencies != {currency}:
        raise PnrPricingAuthorityBackfillError(
            "PQ_CURRENCY_MISMATCH: moneda current de TIR no coincide."
        )

    carriers = {
        _upper(quote.validating_carrier)
        for quote in selection.candidates
        if _upper(quote.validating_carrier)
    }
    if carriers != {expected_carrier}:
        raise PnrPricingAuthorityBackfillError(
            "PQ_CARRIER_MISMATCH: validating carrier current no coincide."
        )

    if preview.currency != currency or preview.total != expected_total:
        raise PnrPricingAuthorityBackfillError(
            "BRAND_PREVIEW_PRICE_MISMATCH: preview same-brand no coincide "
            "con TIR/expected."
        )
    if _upper(preview.validating_carrier) != expected_carrier:
        raise PnrPricingAuthorityBackfillError(
            "BRAND_PREVIEW_CARRIER_MISMATCH: preview same-brand cambió carrier."
        )

    preview_fare_basis = _upper(preview.fare_basis)
    if preview_fare_basis is None:
        raise PnrPricingAuthorityBackfillError(
            "BRAND_PREVIEW_FARE_BASIS_UNKNOWN: no se puede reconciliar fare basis."
        )

    tir_fare_basis: list[str] = []
    for quote in selection.candidates:
        tir_fare_basis.extend(_quote_fare_basis(quote))
    tir_fare_basis = list(dict.fromkeys(tir_fare_basis))
    if not tir_fare_basis or tir_fare_basis != [preview_fare_basis]:
        raise PnrPricingAuthorityBackfillError(
            "FARE_BASIS_MISMATCH: preview same-brand y PQ current no coinciden."
        )

    preview_deadline = _deadline_token(preview.last_day_to_purchase_raw)
    tir_deadlines = [
        value
        for value in (
            _deadline_token(quote.purchase_deadline_raw)
            for quote in selection.candidates
        )
        if value
    ]
    tir_deadlines = list(dict.fromkeys(tir_deadlines))
    if (
        preview_deadline is None
        or len(tir_deadlines) != 1
        or tir_deadlines[0] != preview_deadline
    ):
        raise PnrPricingAuthorityBackfillError(
            "PURCHASE_DEADLINE_MISMATCH: preview y PQ current no comparten "
            "LAST DAY TO PURCHASE exacto."
        )

    return VerifiedPnrPricingAuthorityBackfill(
        booking_id=booking_id,
        confirmation_id=locator,
        price_quote_record_numbers=actual_records,
        brand_code=requested_brand,
        brand_name=(
            str(getattr(fare, "brand_name", "") or "").strip() or None
        ),
        original_total=original_total,
        current_total=current_total,
        currency=currency,
        validating_carrier=expected_carrier,
        fare_basis_codes=tir_fare_basis,
        purchase_deadline_raw=selection.candidates[0].purchase_deadline_raw,
    )
