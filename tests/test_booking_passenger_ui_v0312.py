from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_booking_workspace_loads_passenger_editor() -> None:
    html = Path("app/web/booking.html").read_text(encoding="utf-8")

    assert 'id="passengerForm"' in html
    assert 'id="passengerFields"' in html
    assert 'id="savePassengersButton"' in html
    assert 'id="continueContactButton"' in html
    assert "/app/assets/booking-passengers.js" in html


def test_passenger_editor_uses_booking_passenger_api() -> None:
    script = Path(
        "app/web/assets/booking-passengers.js"
    ).read_text(encoding="utf-8")

    assert "/passengers`" in script
    assert 'method: "PUT"' in script
    assert "passengerState.booking_revision" in script
    assert "collectPassengerPayload" in script


def test_passenger_editor_does_not_send_priced_ptc_fields() -> None:
    script = Path(
        "app/web/assets/booking-passengers.js"
    ).read_text(encoding="utf-8")

    payload_start = script.index("function collectPassengerPayload()")
    payload_end = script.index("\n  async function api", payload_start)
    payload_code = script[payload_start:payload_end]

    assert "passenger_type:" not in payload_code
    assert "quoted_age:" not in payload_code
    assert "quantity:" not in payload_code


def test_passenger_editor_does_not_store_pii_in_browser_storage() -> None:
    script = Path(
        "app/web/assets/booking-passengers.js"
    ).read_text(encoding="utf-8")

    assert "localStorage" not in script
    assert "sessionStorage" not in script


def test_passenger_editor_asset_is_served() -> None:
    with TestClient(app) as client:
        response = client.get("/app/assets/booking-passengers.js")

    assert response.status_code == 200
    assert "Guardar pasajeros" in response.text
