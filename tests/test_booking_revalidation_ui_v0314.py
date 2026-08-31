from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_booking_workspace_has_create_pnr_step() -> None:
    html = Path("app/web/booking.html").read_text(encoding="utf-8")

    assert 'id="createPnrPanel"' in html
    assert 'id="createPnrButton"' in html
    assert 'data-funnel-step="create-pnr"' in html
    assert "Crear PNR" in html
    assert 'id="runRevalidationButton"' not in html
    assert "/app/assets/booking-create-pnr.js" in html
    assert "/app/assets/booking-revalidation.js" not in html


def test_review_hands_off_to_create_pnr() -> None:
    script = Path(
        "app/web/assets/booking-contact-review.js"
    ).read_text(encoding="utf-8")

    assert "continueCreatePnrButton" in script
    assert '"pnr_created"' in script
    assert "Booking listo para crear PNR." in script
    assert "continueRevalidationButton" not in script


def test_create_pnr_ui_uses_canonical_endpoint() -> None:
    script = Path(
        "app/web/assets/booking-create-pnr.js"
    ).read_text(encoding="utf-8")

    assert "/pnr`" in script
    assert 'method: "POST"' in script
    assert "review.booking_revision" in script
    assert "crypto.randomUUID()" in script
    assert "Revalidando automáticamente" in script


def test_create_pnr_post_only_sends_command_fields() -> None:
    script = Path(
        "app/web/assets/booking-create-pnr.js"
    ).read_text(encoding="utf-8")

    marker = "body: JSON.stringify({"
    start = script.index(marker)
    end = script.index("}),", start)
    body = script[start:end]

    assert "revision:" in body
    assert "client_request_id:" in body
    assert "segments" not in body
    assert "fare" not in body
    assert "price" not in body
    assert "booking_class" not in body
    assert "brand" not in body


def test_create_pnr_ui_recovers_persisted_attempt() -> None:
    script = Path(
        "app/web/assets/booking-create-pnr.js"
    ).read_text(encoding="utf-8")

    assert "async function getAttempt()" in script
    assert "error.status === 404" in script
    assert "No reintentes" in script
    assert "reconciliation_required" in script


def test_create_pnr_ui_does_not_store_data_in_browser_storage() -> None:
    script = Path(
        "app/web/assets/booking-create-pnr.js"
    ).read_text(encoding="utf-8")

    assert "localStorage" not in script
    assert "sessionStorage" not in script


def test_create_pnr_asset_is_served() -> None:
    with TestClient(app) as client:
        response = client.get("/app/assets/booking-create-pnr.js")

    assert response.status_code == 200
    assert "Crear PNR" in response.text
    assert "Revalidando automáticamente" in response.text


def test_old_manual_revalidation_asset_is_gone() -> None:
    with TestClient(app) as client:
        response = client.get("/app/assets/booking-revalidation.js")

    assert response.status_code == 404


def test_sabre_http_log_is_generic_for_shared_client() -> None:
    script = Path("app/sabre/client.py").read_text(encoding="utf-8")

    assert "[SABRE] HTTP:" in script
    assert "[SABRE] BFM HTTP:" not in script
