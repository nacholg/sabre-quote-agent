from __future__ import annotations

from app.models.pnr_workspace import (
    PnrAssessment,
    PnrPreIssueReadiness,
    PnrPreIssueReadinessStatus,
    PnrTicketCandidate,
    PnrTicketCandidateStatus,
    PnrWorkspaceStatus,
)


def build_pnr_pre_issue_readiness(
    *,
    confirmation_id: str,
    retrieved_at: str | None,
    stale: bool,
    workspace_status: PnrWorkspaceStatus,
    read_error_code: str | None,
    assessment: PnrAssessment | None,
    ticket_candidate: PnrTicketCandidate | None,
) -> PnrPreIssueReadiness:
    """Decide whether the current workspace response may enter pre-issue.

    This is intentionally not a ticketing authorization and performs no Sabre
    mutation. READY requires a successful remote read for this response, not a
    stale cached snapshot, plus semantic workspace readiness and an explicit
    READY ticket candidate. A later Issue Ticket flow must perform another
    fresh read immediately before any non-idempotent write.
    """

    locator = str(confirmation_id or "").strip().upper()
    blockers: list[str] = []

    if workspace_status == PnrWorkspaceStatus.READ_ERROR or read_error_code:
        blockers.append("READ_ERROR")
    if stale:
        blockers.append("STALE_SNAPSHOT")
    if retrieved_at is None:
        blockers.append("MISSING_RETRIEVED_AT")

    if assessment is None:
        blockers.append("ASSESSMENT_UNAVAILABLE")
    elif assessment.status != PnrWorkspaceStatus.READY_FOR_TICKETING:
        blockers.append("WORKSPACE_NOT_READY")

    if ticket_candidate is None:
        blockers.append("TICKET_CANDIDATE_UNAVAILABLE")
    else:
        if ticket_candidate.status != PnrTicketCandidateStatus.READY:
            blockers.append("TICKET_CANDIDATE_NOT_READY")
        candidate_locator = str(
            ticket_candidate.confirmation_id or ""
        ).strip().upper()
        if locator and candidate_locator and candidate_locator != locator:
            blockers.append("TICKET_CANDIDATE_LOCATOR_MISMATCH")

    fresh_remote_read = (
        not stale
        and workspace_status != PnrWorkspaceStatus.READ_ERROR
        and read_error_code is None
        and retrieved_at is not None
    )
    if not fresh_remote_read:
        blockers.append("FRESH_REMOTE_READ_REQUIRED")

    blockers = list(dict.fromkeys(blockers))
    ready = not blockers
    return PnrPreIssueReadiness(
        status=(
            PnrPreIssueReadinessStatus.READY
            if ready
            else PnrPreIssueReadinessStatus.BLOCKED
        ),
        confirmation_id=locator,
        retrieved_at=retrieved_at,
        fresh_remote_read=fresh_remote_read,
        blockers=blockers,
        message=(
            "El PNR leído en esta sincronización está listo para revisión pre-emisión."
            if ready
            else (
                "El estado actual no permite avanzar a revisión pre-emisión."
            )
        ),
    )
