from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import insert, select

from app.db.models import BookingPnrPricingAuthorityRow
from app.models.pnr_workspace import PnrPricingAuthority
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


TABLE = BookingPnrPricingAuthorityRow.__table__


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PnrPricingAuthorityRepository:
    """Append-only verified current-fare authority history.

    The original accepted Booking offer is never mutated. Every successful
    fare refresh may append one new verified authority record.
    """

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )

    @staticmethod
    def _record(row) -> PnrPricingAuthority:
        return PnrPricingAuthority(
            pricing_authority_id=row["pricing_authority_id"],
            booking_id=row["booking_id"],
            confirmation_id=row["confirmation_id"],
            price_quote_record_numbers=json.loads(
                row["price_quote_record_numbers_json"]
            ),
            brand_code=row["brand_code"],
            brand_name=row["brand_name"],
            original_total=Decimal(row["original_total"]),
            current_total=Decimal(row["current_total"]),
            currency=row["currency"],
            price_difference=Decimal(row["price_difference"]),
            validating_carrier=row["validating_carrier"],
            fare_basis_codes=json.loads(row["fare_basis_codes_json"]),
            purchase_deadline_raw=row["purchase_deadline_raw"],
            provider=row["provider"],
            verified_at=row["verified_at"],
        )

    def latest(
        self,
        booking_id: str,
    ) -> PnrPricingAuthority | None:
        with self.booking_repository.engine.connect() as connection:
            row = (
                connection.execute(
                    select(TABLE)
                    .where(TABLE.c.booking_id == booking_id)
                    .order_by(TABLE.c.pricing_authority_id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return self._record(row) if row is not None else None

    def save(
        self,
        *,
        booking_id: str,
        confirmation_id: str,
        price_quote_record_numbers: list[str],
        brand_code: str,
        brand_name: str | None,
        original_total: Decimal,
        current_total: Decimal,
        currency: str,
        validating_carrier: str | None,
        fare_basis_codes: list[str],
        purchase_deadline_raw: str | None,
        provider: str,
    ) -> PnrPricingAuthority:
        records = [
            str(value).strip()
            for value in price_quote_record_numbers
            if str(value).strip()
        ]
        if not records:
            raise ValueError("Pricing authority requiere al menos un PQ.")
        if len(set(records)) != len(records):
            raise ValueError("Pricing authority contiene PQ duplicados.")

        normalized_brand = str(brand_code or "").strip().upper()
        if not normalized_brand:
            raise ValueError("Pricing authority requiere BrandID exacto.")

        normalized_currency = str(currency or "").strip().upper()
        if len(normalized_currency) != 3:
            raise ValueError("Pricing authority requiere moneda ISO.")

        verified_at = _utc_now()
        difference = current_total - original_total
        values = {
            "booking_id": booking_id,
            "confirmation_id": confirmation_id.strip().upper(),
            "price_quote_record_numbers_json": json.dumps(
                records,
                separators=(",", ":"),
            ),
            "brand_code": normalized_brand,
            "brand_name": (
                str(brand_name).strip()
                if brand_name and str(brand_name).strip()
                else None
            ),
            "original_total": str(original_total),
            "current_total": str(current_total),
            "currency": normalized_currency,
            "price_difference": str(difference),
            "validating_carrier": (
                str(validating_carrier).strip().upper()
                if validating_carrier
                else None
            ),
            "fare_basis_codes_json": json.dumps(
                list(dict.fromkeys(
                    str(value).strip().upper()
                    for value in fare_basis_codes
                    if str(value).strip()
                )),
                separators=(",", ":"),
            ),
            "purchase_deadline_raw": (
                str(purchase_deadline_raw).strip()
                if purchase_deadline_raw
                else None
            ),
            "provider": provider,
            "verified_at": verified_at,
        }

        with self.booking_repository.engine.begin() as connection:
            result = connection.execute(insert(TABLE).values(**values))
            authority_id = result.inserted_primary_key[0]

        with self.booking_repository.engine.connect() as connection:
            row = (
                connection.execute(
                    select(TABLE).where(
                        TABLE.c.pricing_authority_id == authority_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise RuntimeError(
                "No se pudo releer pricing authority persistida."
            )
        return self._record(row)
