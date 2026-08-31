from pathlib import Path

import pytest

from app.config import Settings
from app.main import app
from app.models.booking import BookingStatus
from app.services.booking_state import (
    BookingStateTransitionError,
    can_transition,
    require_transition,
)


CREATE_BOOKING_PATH = "/trip/orders/createBooking"


def test_v0320_can_transition_to_pnr_created_only_from_ready() -> None:
    assert can_transition(
        BookingStatus.READY_TO_CREATE_PNR,
        BookingStatus.PNR_CREATED,
    ) is True

    assert require_transition(
        BookingStatus.READY_TO_CREATE_PNR,
        BookingStatus.PNR_CREATED,
    ) == BookingStatus.PNR_CREATED

    with pytest.raises(BookingStateTransitionError):
        require_transition(
            BookingStatus.READY_FOR_REVIEW,
            BookingStatus.PNR_CREATED,
        )


def test_v0315_prod_allowlist_does_not_enable_create_booking() -> None:
    settings = Settings(
        sabre_client_id="test-client",
        sabre_client_secret="test-secret",
        sabre_pcc="RY3A",
    )

    assert settings.sabre_read_only is True
    assert CREATE_BOOKING_PATH not in settings.allowed_paths
    assert "/v5/offers/shop" in settings.allowed_paths
    assert "/v5/shop/flights/revalidate" in settings.allowed_paths


def test_v0315_fastapi_has_no_pnr_creation_write_route() -> None:
    forbidden_fragments = (
        "create-pnr",
        "create_pnr",
        "createbooking",
        "create-booking",
    )

    for route in app.routes:
        path = str(getattr(route, "path", "")).lower()
        methods = {
            str(method).upper()
            for method in (getattr(route, "methods", None) or set())
        }
        if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue

        assert not any(
            fragment in path
            for fragment in forbidden_fragments
        ), (path, methods)


def test_v0320_create_booking_endpoint_is_centralized_in_config() -> None:
    exact_endpoint = CREATE_BOOKING_PATH.lower()

    offenders = []
    for path in Path("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        if exact_endpoint in text:
            offenders.append(path.as_posix())

    assert offenders == ["app/config.py"]


def test_create_pnr_ui_uses_only_canonical_application_api() -> None:
    html = Path("app/web/booking.html").read_text(
        encoding="utf-8"
    ).lower()
    scripts = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in Path("app/web/assets").glob("booking*.js")
    )

    # v0.33 exposes Create PNR, but the browser must never call
    # Sabre Create Booking directly. Product/PII/pricing remain server-side.
    assert 'id="createpnrbutton"' in html
    assert 'data-funnel-step="create-pnr"' in html
    assert "/bookings/${encodeuricomponent(bookingid)}/pnr" in scripts
    assert CREATE_BOOKING_PATH.lower() not in scripts
    assert "trip/orders/createbooking" not in scripts


def test_revalidation_service_cannot_enter_pnr_created() -> None:
    source = Path(
        "app/services/booking_revalidation_service.py"
    ).read_text(encoding="utf-8")

    assert "BookingStatus.PNR_CREATED" not in source
    assert CREATE_BOOKING_PATH not in source


def test_create_pnr_readiness_gate_is_read_only_contract() -> None:
    source = Path(
        "app/services/booking_create_pnr_readiness_service.py"
    ).read_text(encoding="utf-8")

    assert "class BookingCreatePnrReadinessService" in source
    assert "def get(" in source
    assert "async def create" not in source
    assert "def create(" not in source
    assert CREATE_BOOKING_PATH not in source
