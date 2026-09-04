from pathlib import Path


JS = Path("app/web/assets/pnr-workspace.js")


def test_ui_uses_active_pq_purchase_deadline() -> None:
    source = JS.read_text(encoding="utf-8")

    assert "workspace?.purchase_deadline" in source
    assert "LAST DAY TO PURCHASE vencido" in source
    assert "Time limit operativo" in source
    assert "PURCHASE_DEADLINE_EXPIRED" in source


def test_ui_keeps_ticket_issuance_write_disabled() -> None:
    source = JS.read_text(encoding="utf-8")

    # Explicit same-brand PQ refresh is now allowed; ticket issuance is not.
    assert "/pnr-fare-refresh/apply" in source
    assert 'method: "POST"' in source
    assert 'method: "PUT"' not in source
    assert 'method: "PATCH"' not in source
    assert 'method: "DELETE"' not in source
    assert '$("issueTicketButton")?.addEventListener' not in source
    assert "issueButton.disabled = true" in source
