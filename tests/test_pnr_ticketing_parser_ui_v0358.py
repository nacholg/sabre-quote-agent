from pathlib import Path


def test_ticketing_ui_prefers_structured_arrangement_raw() -> None:
    source = Path("app/web/assets/pnr-workspace.js").read_text(
        encoding="utf-8"
    )

    assert "ticketing.arrangement_raw ||" in source
    assert 'ticketing.ticket_type ||' in source


def test_v0358_does_not_add_ticketing_write_methods() -> None:
    source = Path("app/web/assets/pnr-workspace.js").read_text(
        encoding="utf-8"
    )

    assert 'method: "POST"' not in source
    assert 'method: "PUT"' not in source
    assert 'method: "PATCH"' not in source
    assert 'method: "DELETE"' not in source
