from pathlib import Path


def test_ticketing_ui_prefers_structured_arrangement_raw() -> None:
    source = Path("app/web/assets/pnr-workspace.js").read_text(
        encoding="utf-8"
    )

    assert "ticketing.arrangement_raw ||" in source
    assert 'ticketing.ticket_type ||' in source


def test_v0358_parser_still_does_not_enable_ticket_issuance() -> None:
    source = Path("app/web/assets/pnr-workspace.js").read_text(
        encoding="utf-8"
    )

    # d2 adds an explicit pricing POST, unrelated to the ticketing parser.
    assert "/pnr-fare-refresh/apply" in source
    assert 'method: "POST"' in source
    assert 'method: "PUT"' not in source
    assert 'method: "PATCH"' not in source
    assert 'method: "DELETE"' not in source
    assert '$("issueTicketButton")?.addEventListener' not in source
    assert "issueButton.disabled = true" in source
