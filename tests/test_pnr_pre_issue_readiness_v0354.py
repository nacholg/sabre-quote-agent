from app.models.pnr_workspace import (
    PnrAssessment,
    PnrPreIssueReadinessStatus,
    PnrTicketCandidate,
    PnrTicketCandidateStatus,
    PnrWorkspaceStatus,
)
from app.services.pnr_pre_issue_readiness_service import (
    build_pnr_pre_issue_readiness,
)


def _assessment(
    status: PnrWorkspaceStatus = PnrWorkspaceStatus.READY_FOR_TICKETING,
) -> PnrAssessment:
    return PnrAssessment(status=status)


def _candidate(
    *,
    status: PnrTicketCandidateStatus = PnrTicketCandidateStatus.READY,
    locator: str = "OVFOTM",
) -> PnrTicketCandidate:
    return PnrTicketCandidate(
        status=status,
        confirmation_id=locator,
    )


def _readiness(**overrides):
    values = {
        "confirmation_id": "OVFOTM",
        "retrieved_at": "2026-09-03T18:00:00+00:00",
        "stale": False,
        "workspace_status": PnrWorkspaceStatus.READY_FOR_TICKETING,
        "read_error_code": None,
        "assessment": _assessment(),
        "ticket_candidate": _candidate(),
    }
    values.update(overrides)
    return build_pnr_pre_issue_readiness(**values)


def test_fresh_ready_workspace_can_enter_pre_issue_review() -> None:
    readiness = _readiness()

    assert readiness.status == PnrPreIssueReadinessStatus.READY
    assert readiness.confirmation_id == "OVFOTM"
    assert readiness.fresh_remote_read is True
    assert readiness.blockers == []


def test_stale_cached_snapshot_never_allows_pre_issue() -> None:
    readiness = _readiness(stale=True)

    assert readiness.status == PnrPreIssueReadinessStatus.BLOCKED
    assert readiness.fresh_remote_read is False
    assert "STALE_SNAPSHOT" in readiness.blockers
    assert "FRESH_REMOTE_READ_REQUIRED" in readiness.blockers


def test_read_error_never_allows_pre_issue() -> None:
    readiness = _readiness(
        workspace_status=PnrWorkspaceStatus.READ_ERROR,
        read_error_code="PNR_READ_FAILED",
    )

    assert readiness.status == PnrPreIssueReadinessStatus.BLOCKED
    assert readiness.fresh_remote_read is False
    assert "READ_ERROR" in readiness.blockers


def test_workspace_attention_blocks_pre_issue() -> None:
    readiness = _readiness(
        workspace_status=PnrWorkspaceStatus.NEEDS_ATTENTION,
        assessment=_assessment(PnrWorkspaceStatus.NEEDS_ATTENTION),
    )

    assert readiness.status == PnrPreIssueReadinessStatus.BLOCKED
    assert "WORKSPACE_NOT_READY" in readiness.blockers


def test_blocked_ticket_candidate_blocks_pre_issue() -> None:
    readiness = _readiness(
        ticket_candidate=_candidate(status=PnrTicketCandidateStatus.BLOCKED)
    )

    assert readiness.status == PnrPreIssueReadinessStatus.BLOCKED
    assert "TICKET_CANDIDATE_NOT_READY" in readiness.blockers


def test_candidate_locator_mismatch_blocks_pre_issue() -> None:
    readiness = _readiness(ticket_candidate=_candidate(locator="ABC123"))

    assert readiness.status == PnrPreIssueReadinessStatus.BLOCKED
    assert "TICKET_CANDIDATE_LOCATOR_MISMATCH" in readiness.blockers


def test_missing_retrieval_metadata_blocks_pre_issue() -> None:
    readiness = _readiness(retrieved_at=None)

    assert readiness.status == PnrPreIssueReadinessStatus.BLOCKED
    assert readiness.fresh_remote_read is False
    assert "MISSING_RETRIEVED_AT" in readiness.blockers
    assert "FRESH_REMOTE_READ_REQUIRED" in readiness.blockers


def test_no_snapshot_read_error_has_explicit_blocked_readiness() -> None:
    readiness = build_pnr_pre_issue_readiness(
        confirmation_id="OVFOTM",
        retrieved_at=None,
        stale=False,
        workspace_status=PnrWorkspaceStatus.READ_ERROR,
        read_error_code="PNR_READ_FAILED",
        assessment=None,
        ticket_candidate=None,
    )

    assert readiness.status == PnrPreIssueReadinessStatus.BLOCKED
    assert "READ_ERROR" in readiness.blockers
    assert "ASSESSMENT_UNAVAILABLE" in readiness.blockers
    assert "TICKET_CANDIDATE_UNAVAILABLE" in readiness.blockers
