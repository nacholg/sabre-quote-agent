from __future__ import annotations

from datetime import datetime, timezone

from app.models.pnr_workspace import (
    PnrFinalPreIssueGate,
    PnrFinalPreIssueGateStatus,
    PnrPreIssueReadiness,
    PnrPreIssueReadinessStatus,
    PnrTicketingConstraint,
    PnrTicketingConstraintStatus,
)


def _parse_deadline(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def build_pnr_final_pre_issue_gate(
    *,
    confirmation_id: str,
    pre_issue_readiness: PnrPreIssueReadiness | None,
    ticketing_constraint: PnrTicketingConstraint | None,
    now: datetime | None = None,
) -> PnrFinalPreIssueGate:
    """Compose the final read-only gate before any future ticketing write.

    READY is not an authorization to issue and performs no Sabre mutation.
    It requires the existing pre-issue readiness gate plus a structured,
    timezone-aware ticketing deadline that has not expired.

    Missing ticketing evidence never means "no deadline".
    """

    locator = str(confirmation_id or "").strip().upper()
    evaluated = now or datetime.now(timezone.utc)
    if evaluated.tzinfo is None:
        evaluated = evaluated.replace(tzinfo=timezone.utc)

    blockers: list[str] = []

    if pre_issue_readiness is None:
        blockers.append("PRE_ISSUE_READINESS_UNAVAILABLE")
    else:
        if pre_issue_readiness.status != PnrPreIssueReadinessStatus.READY:
            blockers.append("PRE_ISSUE_NOT_READY")
        readiness_locator = str(
            pre_issue_readiness.confirmation_id or ""
        ).strip().upper()
        if locator and readiness_locator and readiness_locator != locator:
            blockers.append("PRE_ISSUE_LOCATOR_MISMATCH")

    deadline_at: str | None = None
    deadline_expired: bool | None = None
    constraint_status = None

    if ticketing_constraint is None:
        blockers.append("TICKETING_CONSTRAINT_UNAVAILABLE")
    else:
        constraint_status = ticketing_constraint.status
        deadline_at = ticketing_constraint.deadline_at

        if (
            ticketing_constraint.status
            != PnrTicketingConstraintStatus.STRUCTURED_DEADLINE
            or ticketing_constraint.requires_deadline_lookup
        ):
            blockers.append("TICKETING_DEADLINE_UNRESOLVED")
        else:
            deadline = _parse_deadline(ticketing_constraint.deadline_at)
            if deadline is None:
                blockers.append("TICKETING_DEADLINE_UNRESOLVED")
            elif deadline.tzinfo is None:
                blockers.append("TICKETING_DEADLINE_TIMEZONE_UNKNOWN")
            else:
                deadline_expired = deadline <= evaluated
                if deadline_expired:
                    blockers.append("TICKETING_DEADLINE_EXPIRED")

    blockers = list(dict.fromkeys(blockers))
    ready = not blockers

    return PnrFinalPreIssueGate(
        status=(
            PnrFinalPreIssueGateStatus.READY
            if ready
            else PnrFinalPreIssueGateStatus.BLOCKED
        ),
        confirmation_id=locator,
        evaluated_at=evaluated.isoformat(),
        ticketing_constraint_status=constraint_status,
        deadline_at=deadline_at,
        deadline_expired=deadline_expired,
        blockers=blockers,
        message=(
            "Los gates read-only están completos para avanzar a la futura "
            "etapa de emisión. Esto no autoriza ni ejecuta ticketing."
            if ready
            else (
                "La emisión automática futura debe permanecer bloqueada "
                "hasta resolver todos los controles finales."
            )
        ),
    )
