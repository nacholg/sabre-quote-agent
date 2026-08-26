from __future__ import annotations

from app.models.booking import BookingStatus, RevalidationStatus


class BookingStateTransitionError(ValueError):
    """Raised when a Booking state transition violates the funnel contract."""


_ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.DRAFT: {
        BookingStatus.READY_FOR_REVIEW,
        BookingStatus.ABANDONED,
    },
    BookingStatus.READY_FOR_REVIEW: {
        BookingStatus.DRAFT,
        BookingStatus.REVALIDATION_REQUIRED,
        BookingStatus.ABANDONED,
    },
    BookingStatus.REVALIDATION_REQUIRED: {
        BookingStatus.READY_FOR_REVIEW,
        BookingStatus.REQUIRES_AGENT_ACTION,
        BookingStatus.READY_TO_CREATE_PNR,
        BookingStatus.ABANDONED,
    },
    BookingStatus.REQUIRES_AGENT_ACTION: {
        BookingStatus.READY_FOR_REVIEW,
        BookingStatus.REVALIDATION_REQUIRED,
        BookingStatus.READY_TO_CREATE_PNR,
        BookingStatus.ABANDONED,
    },
    BookingStatus.READY_TO_CREATE_PNR: {
        BookingStatus.REVALIDATION_REQUIRED,
        BookingStatus.ABANDONED,
        # Reserved for v0.32. The v0.31 service layer must never call it.
        BookingStatus.PNR_CREATED,
    },
    BookingStatus.ABANDONED: set(),
    BookingStatus.PNR_CREATED: set(),
}


def can_transition(
    current: BookingStatus,
    target: BookingStatus,
) -> bool:
    if current == target:
        return True
    return target in _ALLOWED_TRANSITIONS[current]


def require_transition(
    current: BookingStatus,
    target: BookingStatus,
) -> BookingStatus:
    if not can_transition(current, target):
        raise BookingStateTransitionError(
            f"Transición Booking inválida: {current.value} -> {target.value}."
        )
    return target


def invalidate_after_material_mutation(
    status: BookingStatus,
    revalidation_status: RevalidationStatus,
) -> tuple[BookingStatus, RevalidationStatus]:
    """Invalidate a successful/decisive revalidation after Booking data changes.

    Draft and pre-review edits do not artificially advance the state. Once the
    booking entered the revalidation phase, any material mutation forces a new
    revalidation and marks the previous result stale.
    """

    post_revalidation_states = {
        BookingStatus.REVALIDATION_REQUIRED,
        BookingStatus.REQUIRES_AGENT_ACTION,
        BookingStatus.READY_TO_CREATE_PNR,
    }
    decisive_results = {
        RevalidationStatus.MATCHED,
        RevalidationStatus.PRICE_CHANGED,
        RevalidationStatus.FARE_CHANGED,
        RevalidationStatus.ITINERARY_CHANGED,
        RevalidationStatus.UNAVAILABLE,
        RevalidationStatus.ERROR,
    }

    if status in post_revalidation_states or revalidation_status in decisive_results:
        return (
            BookingStatus.REVALIDATION_REQUIRED,
            RevalidationStatus.STALE,
        )

    return status, revalidation_status
