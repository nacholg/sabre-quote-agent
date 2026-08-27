from __future__ import annotations

from app.models.booking import PnrAttemptStatus


class PnrAttemptTransitionError(ValueError):
    """Raised when Create PNR attempt state would become unsafe."""


_ALLOWED_TRANSITIONS: dict[PnrAttemptStatus, set[PnrAttemptStatus]] = {
    PnrAttemptStatus.PREPARED: {
        PnrAttemptStatus.SUBMITTING,
    },
    PnrAttemptStatus.SUBMITTING: {
        PnrAttemptStatus.SUCCEEDED,
        PnrAttemptStatus.FAILED_SAFE,
        PnrAttemptStatus.RECONCILIATION_REQUIRED,
    },
    # A definitive safe failure may be retried, but it remains the same
    # persistent attempt/idempotency key for the Booking.
    PnrAttemptStatus.FAILED_SAFE: {
        PnrAttemptStatus.SUBMITTING,
    },
    # Reconciliation may discover that Sabre did create the PNR, or prove
    # that no PNR exists and therefore make a later retry safe.
    PnrAttemptStatus.RECONCILIATION_REQUIRED: {
        PnrAttemptStatus.SUCCEEDED,
        PnrAttemptStatus.FAILED_SAFE,
    },
    PnrAttemptStatus.SUCCEEDED: set(),
}


def can_transition_pnr_attempt(
    current: PnrAttemptStatus,
    target: PnrAttemptStatus,
) -> bool:
    if current == target:
        return True
    return target in _ALLOWED_TRANSITIONS[current]


def require_pnr_attempt_transition(
    current: PnrAttemptStatus,
    target: PnrAttemptStatus,
) -> PnrAttemptStatus:
    if not can_transition_pnr_attempt(current, target):
        raise PnrAttemptTransitionError(
            "Transición de intento PNR inválida: "
            f"{current.value} -> {target.value}."
        )
    return target
