from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_booking_workspace_has_revalidation_panel() -> None:
    html = Path("app/web/booking.html").read_text(encoding="utf-8")

    assert 'id="revalidationPanel"' in html
    assert 'id="runRevalidationButton"' in html
    assert 'data-funnel-step="revalidation"' in html
    assert 'class="step locked"' not in html
    assert 'aria-disabled="true"' not in html
    assert "/app/assets/booking-revalidation.js" in html


def test_review_enables_revalidation_from_server_readiness() -> None:
    script = Path(
        "app/web/assets/booking-contact-review.js"
    ).read_text(encoding="utf-8")

    assert 'data.ready_for_review' in script
    assert '"ready_to_create_pnr"' in script
    assert '"requires_agent_action"' in script
    assert 'continueRevalidationButton' in script
    assert 'booking:review-state' in script


def test_revalidation_ui_uses_booking_revalidation_api() -> None:
    script = Path(
        "app/web/assets/booking-revalidation.js"
    ).read_text(encoding="utf-8")

    assert "/revalidation`" in script
    assert 'method: "POST"' in script
    assert "review.booking_revision" in script
    assert "Revalidar con Sabre" in script


def test_revalidation_post_only_sends_booking_revision() -> None:
    script = Path(
        "app/web/assets/booking-revalidation.js"
    ).read_text(encoding="utf-8")

    marker = 'body: JSON.stringify({'
    start = script.index(marker)
    end = script.index("}),", start)
    body = script[start:end]

    assert "revision:" in body
    assert "segments" not in body
    assert "fare" not in body
    assert "price" not in body
    assert "booking_class" not in body


def test_revalidation_ui_contains_no_create_pnr_endpoint() -> None:
    script = Path(
        "app/web/assets/booking-revalidation.js"
    ).read_text(encoding="utf-8").lower()

    assert "/trip/orders/createbooking" not in script
    assert "createpassengernamerecordrq" not in script


def test_revalidation_ui_does_not_store_data_in_browser_storage() -> None:
    script = Path(
        "app/web/assets/booking-revalidation.js"
    ).read_text(encoding="utf-8")

    assert "localStorage" not in script
    assert "sessionStorage" not in script


def test_revalidation_asset_is_served() -> None:
    with TestClient(app) as client:
        response = client.get("/app/assets/booking-revalidation.js")

    assert response.status_code == 200
    assert "Revalidando…" in response.text
    assert "Producto revalidado" in response.text


def test_revalidation_not_run_does_not_claim_no_differences() -> None:
    script = Path(
        "app/web/assets/booking-revalidation.js"
    ).read_text(encoding="utf-8")

    assert 'status !== "matched"' in script
    assert "renderChanges(data.diff, status)" in script


def test_revalidation_labels_offer_ids_explicitly() -> None:
    script = Path(
        "app/web/assets/booking-revalidation.js"
    ).read_text(encoding="utf-8")

    assert "ID oferta origen" in script
    assert "ID oferta candidata" in script


def test_sabre_http_log_is_generic_for_shared_client() -> None:
    script = Path("app/sabre/client.py").read_text(encoding="utf-8")

    assert "[SABRE] HTTP:" in script
    assert "[SABRE] BFM HTTP:" not in script


def test_matched_revalidation_badge_has_strong_green_selector() -> None:
    css = Path("app/web/assets/booking.css").read_text(encoding="utf-8")

    assert ".dev-badge.revalidation-badge.matched" in css
