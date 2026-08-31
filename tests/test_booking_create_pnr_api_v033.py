from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.bookings as bookings_api
from app.main import app
from app.models.booking import (
    BookingPnrAttemptRecord,
    PnrAttemptStatus,
)
from app.services.booking_create_pnr_service import (
    BookingCreatePnrService,
    BookingCreatePnrUnavailableError,
)


def _attempt(client_request_id: str) -> BookingPnrAttemptRecord:
    return BookingPnrAttemptRecord(
        pnr_attempt_id=1,
        booking_id="B-API-TEST",
        client_request_id=client_request_id,
        booking_revision=5,
        accepted_offer_revision_id=2,
        revalidation_id=1,
        environment="cert",
        provider="sabre_booking_management",
        status=PnrAttemptStatus.SUCCEEDED,
        confirmation_id="ABC123",
        provider_reference=None,
        request_fingerprint="abc",
        error_code=None,
        error_message=None,
        created_at="2026-08-31T12:00:00+00:00",
        updated_at="2026-08-31T12:00:01+00:00",
        submitted_at="2026-08-31T12:00:00+00:00",
        completed_at="2026-08-31T12:00:01+00:00",
    )


def test_create_pnr_post_endpoint_returns_persisted_attempt(
    monkeypatch,
):
    request_id = uuid4()

    class FakeService:
        async def execute(self, booking_id, request):
            assert booking_id == "B-API-TEST"
            assert request.revision == 4
            assert request.client_request_id == request_id
            return _attempt(str(request_id))

    monkeypatch.setattr(
        bookings_api,
        "get_booking_create_pnr_service",
        lambda: FakeService(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/bookings/B-API-TEST/pnr",
            json={
                "revision": 4,
                "client_request_id": str(request_id),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["confirmation_id"] == "ABC123"


@pytest.mark.asyncio
async def test_disabled_create_pnr_stops_before_workflow():
    class FakeRepository:
        def get(self, booking_id):
            return SimpleNamespace(environment="cert")

    settings = SimpleNamespace(
        sabre_env="CERT",
        sabre_create_booking_enabled=False,
        sabre_create_booking_prod_enabled=False,
        sabre_create_booking_path="/v1/trip/orders/createBooking",
        sabre_read_only=True,
        allowed_paths=set(),
    )

    service = BookingCreatePnrService(
        booking_repository=FakeRepository(),
        settings_loader=lambda environment: settings,
    )

    with pytest.raises(
        BookingCreatePnrUnavailableError,
        match="deshabilitado",
    ):
        await service.execute(
            "B-API-TEST",
            SimpleNamespace(
                revision=4,
                client_request_id=uuid4(),
            ),
        )


def test_canonical_api_forces_exact_flight_pricing():
    source = Path(
        "app/services/booking_create_pnr_service.py"
    ).read_text(encoding="utf-8")

    assert "include_flight_pricing=True" in source
    assert "BookingCreatePnrWorkflowService" in source


def test_get_create_pnr_attempt_returns_persisted_attempt(
    monkeypatch,
):
    request_id = str(uuid4())

    class FakeRepository:
        def get(self, booking_id):
            assert booking_id == "B-API-TEST"
            return SimpleNamespace(booking_id=booking_id)

    class FakeAttemptService:
        def get(self, booking_id):
            assert booking_id == "B-API-TEST"
            return _attempt(request_id)

    monkeypatch.setattr(
        bookings_api,
        "get_booking_repository",
        lambda: FakeRepository(),
    )
    monkeypatch.setattr(
        bookings_api,
        "get_booking_pnr_attempt_service",
        lambda: FakeAttemptService(),
    )

    with TestClient(app) as client:
        response = client.get(
            "/bookings/B-API-TEST/pnr"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["booking_id"] == "B-API-TEST"
    assert body["status"] == "succeeded"
    assert body["confirmation_id"] == "ABC123"
