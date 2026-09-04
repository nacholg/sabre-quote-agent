from pathlib import Path


JS = Path("app/web/assets/pnr-workspace.js")


def test_pre_issue_review_uses_final_gate_as_authoritative_ui_gate() -> None:
    source = JS.read_text(encoding="utf-8")

    assert "workspace?.final_pre_issue_gate" in source
    assert 'finalGate?.status === "ready"' in source
    assert '"Ticketing deadline"' in source
    assert "TICKETING_DEADLINE_UNRESOLVED" in source
    assert "TICKETING_DEADLINE_EXPIRED" in source
    assert "TICKETING_DEADLINE_TIMEZONE_UNKNOWN" in source


def test_ready_for_ticketing_label_does_not_claim_ticket_can_be_issued() -> None:
    source = JS.read_text(encoding="utf-8")

    assert 'ready_for_ticketing: "PNR verificado"' in source
    assert 'ready_for_ticketing: "Lista para emitir"' not in source


def test_final_gate_blocked_changes_next_action_wording() -> None:
    source = JS.read_text(encoding="utf-8")

    assert '"Revisión de ticketing requerida"' in source
    assert "finalGateBlocked" in source


def test_v0357_ui_still_has_no_ticket_issuance_write() -> None:
    source = JS.read_text(encoding="utf-8")

    # v0.35.11d2 permits one explicit pricing mutation surface, but ticket
    # issuance remains intentionally unavailable.
    assert "/pnr-fare-refresh/apply" in source
    assert 'method: "POST"' in source
    assert 'method: "PUT"' not in source
    assert 'method: "PATCH"' not in source
    assert 'method: "DELETE"' not in source
    assert '$("issueTicketButton")?.addEventListener' not in source
    assert "issueButton.disabled = true" in source
