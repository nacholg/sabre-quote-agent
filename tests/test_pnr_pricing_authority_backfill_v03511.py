from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.pnr_workspace import (
    PnrPassenger,
    PnrPriceQuote,
    PnrSnapshot,
    PnrSpecialService,
)
from app.sabre.soap_brand_pq_store import SabreBrandPriceResult
from app.services.pnr_pricing_authority_backfill_service import (
    PnrPricingAuthorityBackfillError,
    verify_pnr_pricing_authority_backfill,
)


def _fare():
    return SimpleNamespace(
        brand_code="MAINFL",
        brand_name="MAIN CABIN FLEXIBLE",
        total_price=Decimal("781.33"),
        currency="USD",
        validating_carrier="AA",
    )


def _snapshot(
    *,
    record="2",
    total="808.13",
    itinerary_changed=False,
    docs_status="HK",
    fare_basis="SLN7AHM5/L040",
    deadline="LAST DAY TO PURCHASE 05SEP/2359",
):
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        passengers=[
            PnrPassenger(
                name_number="01.01",
                passenger_type="ADT",
            )
        ],
        price_quotes=[
            PnrPriceQuote(
                record_number=record,
                status="ACTIVE",
                validating_carrier="AA",
                passenger_type="ADT",
                passenger_quantity=1,
                passenger_name_numbers=["01.01"],
                total_amount=Decimal(total),
                total_currency="USD",
                fare_basis_codes=[fare_basis],
                purchase_deadline_raw=deadline,
                itinerary_changed=itinerary_changed,
            )
        ],
        special_services=[
            PnrSpecialService(
                code="DOCS",
                status=docs_status,
                name_numbers=["01.01"],
            )
        ],
    )


def _preview(
    *,
    total="808.13",
    fare_basis="SLN7AHM5/L040",
    deadline="05SEP/2359",
):
    return SabreBrandPriceResult(
        currency="USD",
        total=Decimal(total),
        fare_basis=fare_basis,
        validating_carrier="AA",
        last_day_to_purchase_raw=deadline,
        host_command="WPMUSD¥S1*BRMAINFL¥N1.1¥P1ADT",
    )


def _verify(**kwargs):
    return verify_pnr_pricing_authority_backfill(
        booking_id="B-20260831-65FBA856",
        confirmation_id="OVFOTM",
        fare=_fare(),
        snapshot=kwargs.pop("snapshot", _snapshot()),
        requested_brand_code=kwargs.pop("brand", "MAINFL"),
        expected_total=kwargs.pop(
            "expected_total",
            Decimal("808.13"),
        ),
        expected_record_numbers=kwargs.pop("records", ["2"]),
        preview=kwargs.pop("preview", _preview()),
        **kwargs,
    )


def test_real_cert_shape_verifies_backfill() -> None:
    result = _verify()

    assert result.booking_id == "B-20260831-65FBA856"
    assert result.confirmation_id == "OVFOTM"
    assert result.price_quote_record_numbers == ["2"]
    assert result.brand_code == "MAINFL"
    assert result.original_total == Decimal("781.33")
    assert result.current_total == Decimal("808.13")
    assert result.currency == "USD"
    assert result.validating_carrier == "AA"
    assert result.fare_basis_codes == ["SLN7AHM5/L040"]
    assert result.purchase_deadline_raw == (
        "LAST DAY TO PURCHASE 05SEP/2359"
    )


def test_wrong_brand_is_refused() -> None:
    with pytest.raises(
        PnrPricingAuthorityBackfillError,
        match="BRAND_MISMATCH",
    ):
        _verify(brand="MAIN")


def test_wrong_pq_record_is_refused() -> None:
    with pytest.raises(
        PnrPricingAuthorityBackfillError,
        match="PQ_RECORD_MISMATCH",
    ):
        _verify(records=["3"])


def test_missing_docs_is_refused() -> None:
    with pytest.raises(
        PnrPricingAuthorityBackfillError,
        match="SECURE_FLIGHT_DOCS_NOT_COMPLETE",
    ):
        _verify(snapshot=_snapshot(docs_status="NN"))


def test_itinerary_changed_is_refused() -> None:
    with pytest.raises(
        PnrPricingAuthorityBackfillError,
        match="PQ_NOT_CURRENT",
    ):
        _verify(snapshot=_snapshot(itinerary_changed=True))


def test_brand_preview_total_mismatch_is_refused() -> None:
    with pytest.raises(
        PnrPricingAuthorityBackfillError,
        match="BRAND_PREVIEW_PRICE_MISMATCH",
    ):
        _verify(preview=_preview(total="809.13"))


def test_fare_basis_mismatch_is_refused() -> None:
    with pytest.raises(
        PnrPricingAuthorityBackfillError,
        match="FARE_BASIS_MISMATCH",
    ):
        _verify(preview=_preview(fare_basis="OTHER/L040"))


def test_purchase_deadline_mismatch_is_refused() -> None:
    with pytest.raises(
        PnrPricingAuthorityBackfillError,
        match="PURCHASE_DEADLINE_MISMATCH",
    ):
        _verify(preview=_preview(deadline="06SEP/2359"))
