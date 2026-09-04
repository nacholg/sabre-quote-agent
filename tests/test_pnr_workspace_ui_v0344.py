from pathlib import Path


WEB_ROOT = Path("app/web")
ASSETS = WEB_ROOT / "assets"


def test_pnr_workspace_is_a_separate_post_create_page() -> None:
    html = (WEB_ROOT / "pnr-workspace.html").read_text(
        encoding="utf-8"
    )

    assert "PNR Workspace" in html
    assert 'id="pnrWorkspace"' in html
    assert 'id="nextActionTitle"' in html
    assert 'id="assessmentIssues"' in html
    assert 'id="assessmentChecks"' in html
    assert "Ver todos los controles" in html
    assert 'id="pnrSegments"' in html
    assert 'id="pnrPassengers"' in html
    assert 'id="pnrContacts"' in html
    assert 'id="pnrPricing"' in html
    assert 'data-funnel-step=' not in html
    assert "Crear PNR" not in html
    assert "/app/assets/pnr-workspace.js" in html
    assert "/app/assets/pnr-workspace.css" in html


def test_pnr_workspace_reads_stay_get_only_and_issuance_stays_disabled() -> None:
    js = (ASSETS / "pnr-workspace.js").read_text(
        encoding="utf-8"
    )

    assert (
        "/bookings/${encodeURIComponent(bookingId)}/pnr-workspace"
        in js
    )
    assert 'method: "GET"' in js
    # d2 intentionally adds one separately confirmed pricing write action.
    assert "/pnr-fare-refresh/apply" in js
    assert 'method: "POST"' in js
    assert 'method: "PUT"' not in js
    assert 'method: "DELETE"' not in js
    assert "Actualizar desde Sabre" in js
    assert "HX · Requiere revisión" not in js
    assert "Requiere revisión" in js
    assert 'code === "ADTK"' in js
    assert 'code === "OTHS"' in js
    assert "createPnr" not in js
    assert '$("issueTicketButton")?.addEventListener' not in js
    assert "issueButton.disabled = true" in js


def test_main_exposes_pnr_workspace_web_route() -> None:
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert 'WEB_PNR_WORKSPACE = WEB_ROOT / "pnr-workspace.html"' in source
    assert (
        '"/app/bookings/{booking_id}/pnr-workspace"'
        in source
    )
    assert "async def pnr_workspace_web_app" in source
