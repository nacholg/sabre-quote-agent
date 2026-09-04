from pathlib import Path


JS = Path("app/web/assets/pnr-workspace.js")
HTML = Path("app/web/pnr-workspace.html")
CSS = Path("app/web/assets/pnr-workspace.css")


def test_workspace_has_pricing_authority_panel() -> None:
    html = HTML.read_text(encoding="utf-8")

    assert 'id="pricingAuthorityPanel"' in html
    assert 'id="pricingAuthorityTitle"' in html
    assert 'id="pricingAuthorityBadge"' in html
    assert 'id="pricingAuthorityBody"' in html


def test_pricing_authority_ui_preserves_original_and_current_fare() -> None:
    js = JS.read_text(encoding="utf-8")

    assert "function renderPricingAuthority()" in js
    assert "workspace?.pricing_authority" in js
    assert "workspace?.pricing_authority_current === true" in js
    assert "Oferta aceptada original" in js
    assert "Tarifa operativa vigente" in js
    assert "authority.original_total" in js
    assert "authority.current_total" in js
    assert "authority.price_difference" in js
    assert "baseline de auditoría" in js


def test_real_cert_authority_shape_is_presented_as_operational() -> None:
    js = JS.read_text(encoding="utf-8")

    assert "authority.brand_name || authority.brand_code" in js
    assert "authority.price_quote_record_numbers" in js
    assert "authority.validating_carrier" in js
    assert "authority.fare_basis_codes" in js
    assert "authority.verified_at" in js
    assert "PQ ${value}" in js
    assert "· OPERATIVA" in js


def test_stale_authority_is_not_presented_as_current() -> None:
    js = JS.read_text(encoding="utf-8")

    assert '"Autoridad tarifaria no vigente"' in js
    assert '"NO VIGENTE"' in js
    assert "no se usa para habilitar ticketing" in js


def test_pricing_authority_styles_are_present() -> None:
    css = CSS.read_text(encoding="utf-8")

    assert ".pricing-authority-panel{" in css
    assert ".pricing-authority-change{" in css
    assert ".pricing-authority-delta.increase{" in css
    assert ".pricing-authority-note.warning{" in css
    assert ".pricing-card-authority{" in css


def test_d3_is_read_only_ui_and_ticket_issuance_stays_disabled() -> None:
    js = JS.read_text(encoding="utf-8")

    # d2 pricing POST remains the only intentional workspace mutation surface.
    assert "/pnr-fare-refresh/apply" in js
    assert 'method: "POST"' in js
    assert 'method: "PUT"' not in js
    assert 'method: "PATCH"' not in js
    assert 'method: "DELETE"' not in js
    assert '$("issueTicketButton")?.addEventListener' not in js
    assert "issueButton.disabled = true" in js
