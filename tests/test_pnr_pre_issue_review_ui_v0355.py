from pathlib import Path


WEB_ROOT = Path("app/web")
ASSETS = WEB_ROOT / "assets"


def test_pre_issue_review_ui_is_present_but_issue_action_is_disabled() -> None:
    html = (WEB_ROOT / "pnr-workspace.html").read_text(
        encoding="utf-8"
    )

    assert 'id="preIssueReview"' in html
    assert 'id="preIssueBadge"' in html
    assert 'id="preIssueChecks"' in html
    assert 'id="ticketCandidatePassengers"' in html
    assert 'id="preIssueBlockers"' in html
    assert 'id="issueTicketButton"' in html
    assert "Emisión no habilitada en esta versión" in html

    button_start = html.index('id="issueTicketButton"')
    button_fragment = html[button_start:button_start + 350]
    assert "disabled" in button_fragment
    assert 'aria-disabled="true"' in button_fragment


def test_pre_issue_review_consumes_read_only_workspace_contract() -> None:
    js = (ASSETS / "pnr-workspace.js").read_text(
        encoding="utf-8"
    )

    assert "function renderPreIssueReview()" in js
    assert "workspace?.pre_issue_readiness" in js
    assert "workspace?.ticket_candidate" in js
    assert "readiness?.fresh_remote_read === true" in js
    assert "workspace?.stale !== true" in js
    assert 'checkByCode("TICKET_CANDIDATE_READY")' in js
    assert 'checkByCode("ACTIVE_PRICING_SELECTED")' in js
    assert '"PRICING_PASSENGER_COVERAGE"' in js

    # Workspace/pre-issue synchronization remains GET-only. The single POST
    # introduced in d2 is a separately confirmed pricing action, not issuance.
    assert 'method: "GET"' in js
    assert "/pnr-fare-refresh/apply" in js
    assert 'method: "POST"' in js
    assert 'method: "PUT"' not in js
    assert 'method: "PATCH"' not in js
    assert 'method: "DELETE"' not in js

    assert '$("issueTicketButton")?.addEventListener' not in js
    assert "issueButton.disabled = true" in js


def test_pre_issue_dynamic_values_are_html_escaped() -> None:
    js = (ASSETS / "pnr-workspace.js").read_text(
        encoding="utf-8"
    )

    assert '${esc(passenger.name_number' in js
    assert '${esc(passenger.passenger_type' in js
    assert '${esc(passenger.price_quote_record_number' in js
    assert '${blockers.map(item => `<li>${esc(item)}</li>`)' in js


def test_pre_issue_review_has_styles() -> None:
    css = (ASSETS / "pnr-workspace.css").read_text(
        encoding="utf-8"
    )

    assert ".pre-issue-panel{" in css
    assert ".pre-issue-checks{" in css
    assert ".pre-issue-candidate{" in css
    assert ".issue-ticket-disabled{" in css
