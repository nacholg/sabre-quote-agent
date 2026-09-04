from pathlib import Path


JS = Path("app/web/assets/pnr-workspace.js")
HTML = Path("app/web/pnr-workspace.html")
API = Path("app/api/bookings.py")


def test_same_brand_discovery_remains_get_and_read_only() -> None:
    js = JS.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    assert "async function autoRefreshFareIfNeeded()" in js
    assert "/pnr-fare-refresh`" in js
    assert 'method: "GET"' in js

    marker = '"/bookings/{booking_id}/pnr-fare-refresh"'
    position = api.index(marker)
    prefix = api[max(0, position - 100):position]
    assert "@router.get(" in prefix
    assert "@router.post(" not in prefix


def test_apply_refresh_is_explicit_post_with_confirmed_price_identity() -> None:
    js = JS.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    assert "async function applyFareRefresh()" in js
    assert "/pnr-fare-refresh/apply" in js
    assert 'method: "POST"' in js
    assert "window.confirm" in js
    assert "confirm_same_brand_refresh: true" in js
    assert "expected_brand_code: fareRefresh.candidate_brand_code" in js
    assert "expected_currency: fareRefresh.candidate_currency" in js
    assert "expected_total: fareRefresh.candidate_total" in js
    assert 'addEventListener("click", applyFareRefresh)' in js

    marker = '"/bookings/{booking_id}/pnr-fare-refresh/apply"'
    position = api.index(marker)
    prefix = api[max(0, position - 100):position]
    assert "@router.post(" in prefix


def test_workspace_exposes_pricing_write_action_but_not_ticket_issue() -> None:
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")

    assert 'id="fareRefreshAction"' in html
    assert "Guardar nueva PQ same-brand" in js
    assert "NO emite ticket" in js
    assert "No reintentar automáticamente" in js
    assert "issueButton.disabled = true" in js


def test_apply_refresh_is_not_invoked_on_page_load() -> None:
    js = JS.read_text(encoding="utf-8")

    assert js.count("applyFareRefresh()") == 1
    assert 'addEventListener("click", applyFareRefresh)' in js

def test_apply_refresh_sends_stable_client_request_id() -> None:
    js = JS.read_text(encoding="utf-8")

    assert "let fareRefreshClientRequestId = null;" in js
    assert "function newFareRefreshClientRequestId()" in js
    assert "crypto.randomUUID()" in js
    assert "client_request_id: fareRefreshClientRequestId" in js
    assert "fareRefreshClientRequestId ||= newFareRefreshClientRequestId()" in js
