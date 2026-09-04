from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.pnr_workspace import (
    PnrPricingAuthority,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
)


def _upper(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


@dataclass(frozen=True)
class PnrPricingAuthorityResolution:
    authority: PnrPricingAuthority | None
    current: bool | None
    blockers: tuple[str, ...]
    expected_total: Decimal | None
    expected_currency: str | None
    expected_validating_carrier: str | None


def resolve_pnr_pricing_authority(
    *,
    booking_id: str,
    confirmation_id: str,
    fare,
    selection: PnrPricingSelection,
    authority: PnrPricingAuthority | None,
) -> PnrPricingAuthorityResolution:
    original_total = getattr(fare, "total_price", None)
    original_currency = _upper(getattr(fare, "currency", None))
    original_carrier = _upper(getattr(fare, "validating_carrier", None))

    if authority is None:
        return PnrPricingAuthorityResolution(
            authority=None,
            current=None,
            blockers=(),
            expected_total=original_total,
            expected_currency=original_currency,
            expected_validating_carrier=original_carrier,
        )

    blockers: list[str] = []

    if authority.booking_id != booking_id:
        blockers.append("AUTHORITY_BOOKING_MISMATCH")
    if authority.confirmation_id.strip().upper() != str(
        confirmation_id or ""
    ).strip().upper():
        blockers.append("AUTHORITY_LOCATOR_MISMATCH")

    original_brand = _upper(getattr(fare, "brand_code", None))
    if original_brand is None:
        blockers.append("ORIGINAL_BRAND_UNKNOWN")
    elif _upper(authority.brand_code) != original_brand:
        blockers.append("AUTHORITY_BRAND_MISMATCH")

    if original_total is None:
        blockers.append("ORIGINAL_TOTAL_UNKNOWN")
    elif authority.original_total != original_total:
        blockers.append("AUTHORITY_ORIGINAL_TOTAL_MISMATCH")

    if original_currency is None:
        blockers.append("ORIGINAL_CURRENCY_UNKNOWN")
    elif _upper(authority.currency) != original_currency:
        blockers.append("AUTHORITY_CURRENCY_MISMATCH")

    if (
        original_carrier is not None
        and authority.validating_carrier is not None
        and _upper(authority.validating_carrier) != original_carrier
    ):
        blockers.append("AUTHORITY_CARRIER_MISMATCH")

    selected = (
        selection.status == PnrPricingSelectionStatus.SELECTED
        and bool(selection.candidates)
    )
    if not selected:
        blockers.append("AUTHORITY_NO_CURRENT_PRICING")
    else:
        current_records = [
            str(item.record_number or "").strip()
            for item in selection.candidates
        ]
        authority_records = [
            str(item).strip()
            for item in authority.price_quote_record_numbers
        ]
        if (
            any(not value for value in current_records)
            or current_records != authority_records
        ):
            blockers.append("AUTHORITY_PQ_RECORD_MISMATCH")

        if any(
            item.itinerary_changed is not False
            for item in selection.candidates
        ):
            blockers.append("AUTHORITY_PQ_NOT_CURRENT")

        totals = [item.total_amount for item in selection.candidates]
        if any(value is None for value in totals):
            blockers.append("AUTHORITY_PQ_TOTAL_UNKNOWN")
        else:
            current_total = sum(
                (value for value in totals if value is not None),
                Decimal("0"),
            )
            if current_total != authority.current_total:
                blockers.append("AUTHORITY_CURRENT_TOTAL_MISMATCH")

        currencies = {
            _upper(item.total_currency)
            for item in selection.candidates
            if _upper(item.total_currency)
        }
        if currencies != {_upper(authority.currency)}:
            blockers.append("AUTHORITY_PQ_CURRENCY_MISMATCH")

        if authority.validating_carrier:
            carriers = {
                _upper(item.validating_carrier)
                for item in selection.candidates
                if _upper(item.validating_carrier)
            }
            if carriers != {_upper(authority.validating_carrier)}:
                blockers.append("AUTHORITY_PQ_CARRIER_MISMATCH")

        authority_fares = {
            str(value).strip().upper()
            for value in authority.fare_basis_codes
            if str(value).strip()
        }
        if authority_fares:
            current_fares = {
                str(value).strip().upper()
                for item in selection.candidates
                for value in item.fare_basis_codes
                if str(value).strip()
            }
            if current_fares != authority_fares:
                blockers.append("AUTHORITY_FARE_BASIS_MISMATCH")

        if authority.purchase_deadline_raw:
            deadlines = {
                str(item.purchase_deadline_raw or "").strip()
                for item in selection.candidates
                if str(item.purchase_deadline_raw or "").strip()
            }
            if authority.purchase_deadline_raw.strip() not in deadlines:
                blockers.append("AUTHORITY_PURCHASE_DEADLINE_MISMATCH")

    blockers = list(dict.fromkeys(blockers))
    if blockers:
        return PnrPricingAuthorityResolution(
            authority=authority,
            current=False,
            blockers=tuple(blockers),
            expected_total=original_total,
            expected_currency=original_currency,
            expected_validating_carrier=original_carrier,
        )

    return PnrPricingAuthorityResolution(
        authority=authority,
        current=True,
        blockers=(),
        expected_total=authority.current_total,
        expected_currency=_upper(authority.currency),
        expected_validating_carrier=(
            _upper(authority.validating_carrier)
            or original_carrier
        ),
    )
