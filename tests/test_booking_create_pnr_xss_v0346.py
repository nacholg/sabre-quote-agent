from pathlib import Path


SOURCE = Path("app/web/assets/booking-create-pnr.js")


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_create_pnr_html_escapes_provider_and_revalidation_values() -> None:
    source = _source()

    assert "function esc(value)" in source
    assert '${esc(attempt.error_message)}' in source
    assert '${esc(attempt.confirmation_id || "—")}' in source
    assert "${esc(attempt.confirmation_id)}" in source
    assert '${esc(change.field || "Cambio")}' in source
    assert '${esc(change.before ?? "—")}' in source
    assert '${esc(change.after ?? "—")}' in source
    assert "${esc(data.revalidation_status)}" in source


def test_create_pnr_does_not_render_known_server_values_raw() -> None:
    source = _source()

    assert (
        '<div class="revalidation-error-detail">'
        '${attempt.error_message}</div>'
        not in source
    )
    assert "${String(change.before ?? \"—\")}" not in source
    assert "${String(change.after ?? \"—\")}" not in source
    assert "<strong>${data.revalidation_status}</strong>" not in source
