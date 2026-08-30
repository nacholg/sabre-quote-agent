import pytest

from app.models.booking import BookingStatus, RevalidationStatus
from app.services.booking_state import (
    BookingStateTransitionError,
    invalidate_after_material_mutation,
    require_transition,
)


def test_booking_happy_path_states_are_allowed() -> None:
    assert require_transition(
        BookingStatus.DRAFT,
        BookingStatus.READY_FOR_REVIEW,
    ) == BookingStatus.READY_FOR_REVIEW
    assert require_transition(
        BookingStatus.READY_FOR_REVIEW,
        BookingStatus.REVALIDATION_REQUIRED,
    ) == BookingStatus.REVALIDATION_REQUIRED
    assert require_transition(
        BookingStatus.REVALIDATION_REQUIRED,
        BookingStatus.READY_TO_CREATE_PNR,
    ) == BookingStatus.READY_TO_CREATE_PNR


def test_booking_cannot_skip_from_draft_to_ready_to_create_pnr() -> None:
    with pytest.raises(BookingStateTransitionError):
        require_transition(
            BookingStatus.DRAFT,
            BookingStatus.READY_TO_CREATE_PNR,
        )


def test_terminal_states_cannot_reopen() -> None:
    with pytest.raises(BookingStateTransitionError):
        require_transition(
            BookingStatus.ABANDONED,
            BookingStatus.DRAFT,
        )
    with pytest.raises(BookingStateTransitionError):
        require_transition(
            BookingStatus.PNR_CREATED,
            BookingStatus.DRAFT,
        )


def test_material_mutation_invalidates_successful_revalidation() -> None:
    status, revalidation = invalidate_after_material_mutation(
        BookingStatus.READY_TO_CREATE_PNR,
        RevalidationStatus.MATCHED,
    )

    assert status == BookingStatus.REVALIDATION_REQUIRED
    assert revalidation == RevalidationStatus.STALE


def test_draft_edit_does_not_fake_revalidation_state() -> None:
    status, revalidation = invalidate_after_material_mutation(
        BookingStatus.DRAFT,
        RevalidationStatus.NOT_RUN,
    )

    assert status == BookingStatus.DRAFT
    assert revalidation == RevalidationStatus.NOT_RUN
