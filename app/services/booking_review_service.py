from __future__ import annotations

from app.models.booking import (
    BookingReviewResponse,
    BookingStatus,
)
from app.services.booking_contact_service import (
    BookingContactService,
)
from app.services.booking_passenger_service import (
    BookingPassengerService,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


class BookingReviewService:
    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )

    def get(self, booking_id: str) -> BookingReviewResponse:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)

        offer_revision = booking.accepted_offer_revision
        if offer_revision is None:
            raise RuntimeError(
                "El Booking no tiene una revisión de oferta aceptada."
            )

        passengers = BookingPassengerService(
            booking_repository=self.booking_repository
        ).get(booking_id)
        contact = BookingContactService(
            booking_repository=self.booking_repository
        ).get(booking_id)

        ready = (
            passengers.complete
            and contact.complete
            and booking.status == BookingStatus.READY_FOR_REVIEW
        )

        return BookingReviewResponse(
            booking_id=booking.booking_id,
            booking_revision=booking.revision,
            status=booking.status,
            revalidation_status=booking.revalidation_status,
            ready_for_review=ready,
            passengers_complete=passengers.complete,
            contact_complete=contact.complete,
            offer_revision=offer_revision,
            passengers=passengers.passengers,
            contact=contact,
        )


def get_booking_review_service() -> BookingReviewService:
    return BookingReviewService()
