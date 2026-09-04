from datetime import datetime, timezone

from app.models.pnr_workspace import (
    PnrFinalPreIssueGateStatus,
    PnrPreIssueReadiness,
    PnrPreIssueReadinessStatus,
    PnrTicketingConstraint,
    PnrTicketingConstraintStatus,
)
from app.services.pnr_final_pre_issue_gate_service import (
    build_pnr_final_pre_issue_gate,
)


NOW = datetime(2026, 9, 3, 20, 30, tzinfo=timezone.utc)


def _ready_pre_issue() -> PnrPreIssueReadiness:
    return PnrPreIssueReadiness(
        status=PnrPreIssueReadinessStatus.READY,
        confirmation_id="OVFOTM",
        retrieved_at="2026-09-03T20:28:45+00:00",
        fresh_remote_read=True,
    )


def _structured_deadline(value: str) -> PnrTicketingConstraint:
    return PnrTicketingConstraint(
        status=PnrTicketingConstraintStatus.STRUCTURED_DEADLINE,
        deadline_at=value,
        deadline_interpretable=True,
        requires_deadline_lookup=False,
    )


def test_real_cert_shape_adtk_without_deadline_is_blocked() -> None:
    constraint = PnrTicketingConstraint(
        status=PnrTicketingConstraintStatus.ADVISORY_WITHOUT_DEADLINE,
        advisory_present=True,
        advisory_airline_code="1S",
        advisory_code="ADTK",
        advisory_status="KK",
        requires_deadline_lookup=True,
    )

    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=constraint,
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.BLOCKED
    assert result.deadline_at is None
    assert result.deadline_expired is None
    assert result.blockers == ["TICKETING_DEADLINE_UNRESOLVED"]


def test_future_timezone_aware_structured_deadline_is_ready() -> None:
    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=_structured_deadline(
            "2026-09-10T18:00:00+00:00"
        ),
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.READY
    assert result.deadline_expired is False
    assert result.blockers == []


def test_expired_structured_deadline_is_blocked() -> None:
    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=_structured_deadline(
            "2026-09-03T20:29:59+00:00"
        ),
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.BLOCKED
    assert result.deadline_expired is True
    assert result.blockers == ["TICKETING_DEADLINE_EXPIRED"]


def test_deadline_equal_to_now_is_expired_for_safety() -> None:
    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=_structured_deadline(
            "2026-09-03T20:30:00+00:00"
        ),
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.BLOCKED
    assert result.deadline_expired is True


def test_naive_structured_deadline_never_assumes_timezone() -> None:
    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=_structured_deadline(
            "2026-09-10T18:00:00"
        ),
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.BLOCKED
    assert result.deadline_expired is None
    assert result.blockers == [
        "TICKETING_DEADLINE_TIMEZONE_UNKNOWN"
    ]


def test_no_structured_constraint_is_blocked_not_treated_as_unlimited() -> None:
    constraint = PnrTicketingConstraint(
        status=PnrTicketingConstraintStatus.NO_STRUCTURED_CONSTRAINT,
        requires_deadline_lookup=True,
    )

    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=constraint,
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.BLOCKED
    assert "TICKETING_DEADLINE_UNRESOLVED" in result.blockers


def test_pre_issue_blocked_stays_blocked_even_with_future_deadline() -> None:
    pre_issue = PnrPreIssueReadiness(
        status=PnrPreIssueReadinessStatus.BLOCKED,
        confirmation_id="OVFOTM",
        fresh_remote_read=False,
        blockers=["FRESH_REMOTE_READ_REQUIRED"],
    )

    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=pre_issue,
        ticketing_constraint=_structured_deadline(
            "2026-09-10T18:00:00+00:00"
        ),
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.BLOCKED
    assert "PRE_ISSUE_NOT_READY" in result.blockers


def test_missing_constraint_is_blocked() -> None:
    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=None,
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.BLOCKED
    assert result.blockers == ["TICKETING_CONSTRAINT_UNAVAILABLE"]


def test_pre_issue_locator_mismatch_is_blocked() -> None:
    pre_issue = _ready_pre_issue().model_copy(
        update={"confirmation_id": "ABC123"}
    )

    result = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=pre_issue,
        ticketing_constraint=_structured_deadline(
            "2026-09-10T18:00:00+00:00"
        ),
        now=NOW,
    )

    assert result.status == PnrFinalPreIssueGateStatus.BLOCKED
    assert "PRE_ISSUE_LOCATOR_MISMATCH" in result.blockers
