from decimal import Decimal

from app.services.booking_repository import BookingRepository
from app.services.pnr_pricing_authority_repository import (
    PnrPricingAuthorityRepository,
)


def _repo(tmp_path):
    booking_repo = BookingRepository(
        db_path=tmp_path / "authority.db"
    )
    return PnrPricingAuthorityRepository(
        booking_repository=booking_repo
    )


def test_pricing_authority_is_append_only_and_latest_wins(tmp_path) -> None:
    repo = _repo(tmp_path)

    first = repo.save(
        booking_id="B-1",
        confirmation_id="ABC123",
        price_quote_record_numbers=["1"],
        brand_code="MAINFL",
        brand_name="MAIN CABIN FLEXIBLE",
        original_total=Decimal("781.33"),
        current_total=Decimal("808.13"),
        currency="USD",
        validating_carrier="AA",
        fare_basis_codes=["SLN7AHM5/L040"],
        purchase_deadline_raw="LAST DAY TO PURCHASE 05SEP/2359",
        provider="sabre_brand_pq_store",
    )

    second = repo.save(
        booking_id="B-1",
        confirmation_id="ABC123",
        price_quote_record_numbers=["3"],
        brand_code="MAINFL",
        brand_name="MAIN CABIN FLEXIBLE",
        original_total=Decimal("781.33"),
        current_total=Decimal("799.00"),
        currency="USD",
        validating_carrier="AA",
        fare_basis_codes=["SLN7AHM5/L040"],
        purchase_deadline_raw="LAST DAY TO PURCHASE 06SEP/2359",
        provider="sabre_brand_pq_store",
    )

    assert first.pricing_authority_id < second.pricing_authority_id
    latest = repo.latest("B-1")
    assert latest is not None
    assert latest.price_quote_record_numbers == ["3"]
    assert latest.current_total == Decimal("799.00")
    assert latest.original_total == Decimal("781.33")
    assert latest.price_difference == Decimal("17.67")


def test_price_decrease_is_preserved_as_negative_delta(tmp_path) -> None:
    repo = _repo(tmp_path)

    saved = repo.save(
        booking_id="B-2",
        confirmation_id="DEF456",
        price_quote_record_numbers=["2"],
        brand_code="MAINFL",
        brand_name=None,
        original_total=Decimal("781.33"),
        current_total=Decimal("760.00"),
        currency="USD",
        validating_carrier="AA",
        fare_basis_codes=[],
        purchase_deadline_raw=None,
        provider="test",
    )

    assert saved.price_difference == Decimal("-21.33")
