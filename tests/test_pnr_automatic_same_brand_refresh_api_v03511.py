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
from app.services.pnr_pricing_refresh_attempt_service import (
    PnrPricingRefreshAttemptIdempotencyError,
)


def _request(*, confirm: bool = True):
    return PnrAutomaticSameBrandRefreshRequest(
        confirm_same_brand_refresh=confirm,
        client_request_id="req-ui-1",
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
        async def execute(self, booking_id: str, request):
            raise AssertionError("service must not run without confirmation")

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_pricing_refresh_execution_service",
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


def test_apply_fare_refresh_passes_whole_idempotent_request(monkeypatch) -> None:
    expected = _response()
    captured = {}

    class Service:
        async def execute(self, booking_id: str, request):
            captured["booking_id"] = booking_id
            captured["request"] = request
            return expected

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_pricing_refresh_execution_service",
        lambda: Service(),
    )

    request = _request()
    actual = asyncio.run(
        bookings_api.apply_booking_pnr_fare_refresh(
            "B-TEST",
            request,
        )
    )

    assert actual == expected
    assert captured["booking_id"] == "B-TEST"
    assert captured["request"] == request
    assert captured["request"].client_request_id == "req-ui-1"


def test_apply_fare_refresh_maps_missing_booking_to_404(monkeypatch) -> None:
    class Service:
        async def execute(self, booking_id: str, request):
            raise KeyError(booking_id)

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_pricing_refresh_execution_service",
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


def test_apply_fare_refresh_maps_idempotency_mismatch_to_409(
    monkeypatch,
) -> None:
    class Service:
        async def execute(self, booking_id: str, request):
            raise PnrPricingRefreshAttemptIdempotencyError("request reused")

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_pricing_refresh_execution_service",
        lambda: Service(),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            bookings_api.apply_booking_pnr_fare_refresh(
                "B-TEST",
                _request(),
            )
        )

    assert exc_info.value.status_code == 409
