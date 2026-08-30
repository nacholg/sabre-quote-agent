from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_shopping_loads_booking_handoff_script() -> None:
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    assert "/app/assets/booking-handoff.js" in html


def test_booking_shell_has_four_step_funnel_without_create_pnr() -> None:
    html = Path("app/web/booking.html").read_text(encoding="utf-8")

    for label in ("Pasajeros", "Contacto", "Review", "Revalidación"):
        assert label in html

    assert "create-pnr" not in html.lower()
    assert "Continuar con pasajeros" in html


def test_booking_handoff_requires_persisted_exact_fare() -> None:
    script = Path(
        "app/web/assets/booking-handoff.js"
    ).read_text(encoding="utf-8")

    assert "persistedFareSelection" in script
    assert "selection.fare_index" in script
    assert "shownFareIndex" in script
    assert "client_request_id" in script
    assert "/bookings" in script


def test_booking_workspace_and_assets_are_served() -> None:
    with TestClient(app) as client:
        workspace = client.get("/app/bookings/B-TEST")
        css = client.get("/app/assets/booking.css")
        js = client.get("/app/assets/booking.js")
        handoff = client.get("/app/assets/booking-handoff.js")

    assert workspace.status_code == 200
    assert "Booking workspace" in workspace.text
    assert css.status_code == 200
    assert js.status_code == 200
    assert handoff.status_code == 200


def test_booking_workspace_preserves_airport_local_times() -> None:
    script = Path("app/web/assets/booking.js").read_text(
        encoding="utf-8"
    )

    assert "new Date(value)" not in script
    assert "Sabre segment timestamps represent local airport wall-clock time" in script
    assert "const match = raw.match" in script
