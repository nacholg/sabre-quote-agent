import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api import bookings as bookings_api
from app.models.pnr_workspace import (
    PnrAutomaticSameBrandRefreshRequest,
    PnrAutomaticSameBrandRefreshResponse,
    PnrAutomaticSameBrandRefreshStatus,
)


def _request(*, confirm: bool = True):
    return PnrAutomaticSameBrandRefreshRequest(
        confirm_same_brand_refresh=confirm,
        expected_brand_code="MAINFL",
        expected_currency="USD",
        expected_total=Decimal("808.13"),
    )


def _response():
    return PnrAutomaticSameBrandRefreshResponse(
        booking_id="B-TEST",
        confirmation_id="OVFOTM",
        status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
        brand_code="MAINFL",
        source_total=Decimal("781.33"),
        candidate_total=Decimal("808.13"),
        price_difference=Decimal("26.80"),
        sabre_mutation_performed=False,
        blockers=["PNR_PRICING_GATE_DISABLED"],
    )


def test_apply_fare_refresh_requires_explicit_confirmation(monkeypatch) -> None:
    class Service:
        async def refresh(self, booking_id: str, **kwargs):
            raise AssertionError("service must not run without confirmation")

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_automatic_same_brand_refresh_service",
        lambda: Service(),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            bookings_api.apply_booking_pnr_fare_refresh(
                "B-TEST",
                _request(confirm=False),
            )
        )

    assert exc_info.value.status_code == 409


def test_apply_fare_refresh_passes_confirmed_candidate_identity(monkeypatch) -> None:
    expected = _response()
    captured = {}

    class Service:
        async def refresh(self, booking_id: str, **kwargs):
            captured["booking_id"] = booking_id
            captured.update(kwargs)
            return expected

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_automatic_same_brand_refresh_service",
        lambda: Service(),
    )

    actual = asyncio.run(
        bookings_api.apply_booking_pnr_fare_refresh(
            "B-TEST",
            _request(),
        )
    )

    assert actual == expected
    assert captured == {
        "booking_id": "B-TEST",
        "expected_brand_code": "MAINFL",
        "expected_currency": "USD",
        "expected_total": Decimal("808.13"),
    }


def test_apply_fare_refresh_maps_missing_booking_to_404(monkeypatch) -> None:
    class Service:
        async def refresh(self, booking_id: str, **kwargs):
            raise KeyError(booking_id)

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_automatic_same_brand_refresh_service",
        lambda: Service(),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            bookings_api.apply_booking_pnr_fare_refresh(
                "MISSING",
                _request(),
            )
        )

    assert exc_info.value.status_code == 404
