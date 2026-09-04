from pathlib import Path


JS = Path("app/web/assets/pnr-workspace.js")
HTML = Path("app/web/pnr-workspace.html")
API = Path("app/api/bookings.py")


def test_workspace_auto_refreshes_expired_fare_read_only() -> None:
    source = JS.read_text(encoding="utf-8")

    assert "needsFareRefresh()" in source
    assert "pnr-fare-refresh" in source
    assert "Tarifa expirada · recotizando" in source
    assert "Nueva tarifa encontrada" in source
    assert "MISMA BRAND" in source
    assert "El PNR no fue modificado." in source


def test_fare_refresh_ui_keeps_zero_write_contract() -> None:
    source = JS.read_text(encoding="utf-8")

    assert 'method: "POST"' not in source
    assert 'method: "PUT"' not in source
    assert 'method: "PATCH"' not in source
    assert 'method: "DELETE"' not in source
    assert "issueButton.disabled = true" in source


def test_fare_refresh_endpoint_is_get() -> None:
    source = API.read_text(encoding="utf-8")

    marker = '"/bookings/{booking_id}/pnr-fare-refresh"'
    position = source.index(marker)
    prefix = source[max(0, position - 80):position]
    assert "@router.get(" in prefix
    assert "@router.post(" not in prefix


def test_workspace_has_fare_refresh_panel() -> None:
    source = HTML.read_text(encoding="utf-8")

    assert 'id="fareRefreshPanel"' in source
    assert 'id="fareRefreshTitle"' in source
    assert 'id="fareRefreshBody"' in source
