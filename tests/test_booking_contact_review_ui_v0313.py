from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_booking_workspace_has_contact_and_review_panels() -> None:
    html = Path("app/web/booking.html").read_text(encoding="utf-8")

    assert 'id="contactPanel"' in html
    assert 'id="contactForm"' in html
    assert 'id="reviewPanel"' in html
    assert 'id="reviewContent"' in html
    assert "/app/assets/booking-contact-review.js" in html


def test_revalidation_step_remains_locked_in_v0313() -> None:
    html = Path("app/web/booking.html").read_text(encoding="utf-8")

    assert 'data-funnel-step="revalidation"' in html
    assert 'aria-disabled="true"' in html
    assert "Revalidación · siguiente iteración" in html
    assert "create-pnr" not in html.lower()


def test_contact_review_ui_uses_canonical_backend_endpoints() -> None:
    script = Path(
        "app/web/assets/booking-contact-review.js"
    ).read_text(encoding="utf-8")

    assert "/contact`" in script
    assert "/review`" in script
    assert 'method: "PUT"' in script
    assert "contactState.booking_revision" in script
    assert "ready_for_review" in script


def test_contact_review_ui_does_not_store_pii_in_browser_storage() -> None:
    script = Path(
        "app/web/assets/booking-contact-review.js"
    ).read_text(encoding="utf-8")

    assert "localStorage" not in script
    assert "sessionStorage" not in script


def test_passenger_and_contact_scripts_share_booking_revision() -> None:
    passenger_script = Path(
        "app/web/assets/booking-passengers.js"
    ).read_text(encoding="utf-8")
    contact_script = Path(
        "app/web/assets/booking-contact-review.js"
    ).read_text(encoding="utf-8")

    assert "booking:revision-changed" in passenger_script
    assert "booking:passengers-state" in passenger_script
    assert "booking:revision-changed" in contact_script
    assert "booking:passengers-state" in contact_script


def test_contact_review_asset_is_served() -> None:
    with TestClient(app) as client:
        response = client.get("/app/assets/booking-contact-review.js")

    assert response.status_code == 200
    assert "Guardar contacto" in response.text
    assert "Booking listo para pasar a revalidación." in response.text
