from pathlib import Path


SOURCE = Path("app/web/assets/booking-create-pnr.js")


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_post_create_redirect_requires_succeeded_attempt_and_locator() -> None:
    source = _source()

    start = source.index("function isWorkspaceReady")
    end = source.index("function transitionToWorkspace")
    guard = source[start:end]

    assert 'attempt?.status === "succeeded"' in guard
    assert "confirmation_id" in guard
    assert "reconciliation_required" not in guard
    assert "submitting" not in guard
    assert "failed_safe" not in guard


def test_success_replaces_funnel_with_pnr_workspace() -> None:
    source = _source()

    assert (
        "`/app/bookings/${encodeURIComponent(bookingId)}`"
        in source
    )
    assert "`/pnr-workspace`" in source
    assert "window.location.replace(workspaceUrl())" in source


def test_render_attempt_transitions_only_through_guard() -> None:
    source = _source()

    start = source.index("function renderAttempt")
    end = source.index(
        "async function renderLatestRevalidationFailure",
        start,
    )
    body = source[start:end]

    assert "transitionToWorkspace(attempt)" in body


def test_page_load_recovers_existing_persisted_success() -> None:
    source = _source()

    assert "async function redirectPersistedSuccess()" in source
    assert "const attempt = await getAttempt();" in source
    assert "redirectPersistedSuccess();" in source


def test_ambiguous_lookup_failure_does_not_force_navigation() -> None:
    source = _source()

    start = source.index("async function redirectPersistedSuccess")
    end = source.index(
        'window.addEventListener("booking:review-state"',
        start,
    )
    recovery = source[start:end]

    assert "try {" in recovery
    assert "catch {" in recovery
    assert "window.location.replace" not in recovery
