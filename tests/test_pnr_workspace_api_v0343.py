import asyncio

import pytest
from fastapi import HTTPException

from app.api import bookings as bookings_api
from app.models.pnr_workspace import (
    PnrWorkspaceResponse,
    PnrWorkspaceStatus,
)
from app.services.pnr_workspace_service import PnrWorkspaceStateError


def _response() -> PnrWorkspaceResponse:
    return PnrWorkspaceResponse(
        booking_id="B-TEST",
        confirmation_id="OVFOTM",
        provider="sabre_travel_itinerary_read",
        environment="cert",
        status=PnrWorkspaceStatus.READ_ERROR,
        read_error_code="PNR_READ_FAILED",
        read_error_message="Sincronización pendiente.",
    )


def test_pnr_workspace_endpoint_returns_service_contract(
    monkeypatch,
) -> None:
    expected = _response()

    class Service:
        def get(self, booking_id: str):
            assert booking_id == "B-TEST"
            return expected

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_workspace_service",
        lambda: Service(),
    )

    actual = asyncio.run(
        bookings_api.get_booking_pnr_workspace("B-TEST")
    )
    assert actual == expected


def test_pnr_workspace_endpoint_maps_state_error_to_409(
    monkeypatch,
) -> None:
    class Service:
        def get(self, booking_id: str):
            raise PnrWorkspaceStateError("not ready")

    monkeypatch.setattr(
        bookings_api,
        "get_pnr_workspace_service",
        lambda: Service(),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            bookings_api.get_booking_pnr_workspace("B-TEST")
        )

    assert exc_info.value.status_code == 409
