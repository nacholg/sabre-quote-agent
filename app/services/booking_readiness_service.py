from __future__ import annotations

import re

from sqlalchemy import select

from app.db.models import BookingContactRow, BookingPassengerRow
from app.models.booking import (
    BookingRecord,
    BookingStatus,
    RevalidationStatus,
)
from app.models.quote_request import PassengerKind
from app.services.booking_repository import BookingRepository
from app.services.booking_state import invalidate_after_material_mutation


CONTACT_TABLE = BookingContactRow.__table__
PASSENGER_TABLE = BookingPassengerRow.__table__

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def contact_values_complete(
    *,
    name: str | None,
    email: str | None,
    phone_country_code: str | None,
    phone_number: str | None,
) -> bool:
    if not all((name, email, phone_country_code, phone_number)):
        return False

    if _EMAIL_RE.fullmatch(str(email)) is None:
        return False

    country_digits = str(phone_country_code).lstrip("+")
    phone_digits = str(phone_number)
    return (
        country_digits.isdigit()
        and 1 <= len(country_digits) <= 4
        and phone_digits.isdigit()
        and 6 <= len(phone_digits) <= 15
    )


def contact_complete(
    repository: BookingRepository,
    booking_id: str,
) -> bool:
    with repository.engine.connect() as connection:
        row = (
            connection.execute(
                select(CONTACT_TABLE).where(
                    CONTACT_TABLE.c.booking_id == booking_id
                )
            )
            .mappings()
            .first()
        )

    if row is None:
        return False

    return contact_values_complete(
        name=row["name"],
        email=row["email"],
        phone_country_code=row["phone_country_code"],
        phone_number=row["phone_number"],
    )


def passengers_complete(
    repository: BookingRepository,
    booking: BookingRecord,
) -> bool:
    revision = booking.accepted_offer_revision
    if revision is None:
        return False

    expected_count = sum(
        item.quantity
        for item in revision.snapshot.passenger_mix
    )
    if expected_count < 1:
        return False

    with repository.engine.connect() as connection:
        rows = (
            connection.execute(
                select(PASSENGER_TABLE)
                .where(
                    PASSENGER_TABLE.c.booking_id == booking.booking_id
                )
                .order_by(PASSENGER_TABLE.c.slot_index)
            )
            .mappings()
            .all()
        )

    if len(rows) != expected_count:
        return False

    expected_slots = set(range(1, expected_count + 1))
    if {int(row["slot_index"]) for row in rows} != expected_slots:
        return False

    adult_count = sum(
        1
        for row in rows
        if row["passenger_type"] == PassengerKind.ADULT.value
    )

    for row in rows:
        if not (
            row["given_name"]
            and row["surname"]
            and row["date_of_birth"]
        ):
            return False

        if (
            row["passenger_type"] == PassengerKind.INFANT.value
            and adult_count > 1
            and row["associated_adult_slot_index"] is None
        ):
            return False

    return True


def resolve_after_material_booking_data_mutation(
    booking: BookingRecord,
    *,
    passengers_are_complete: bool,
    contact_is_complete: bool,
) -> tuple[BookingStatus, RevalidationStatus]:
    """Resolve funnel state after passenger/contact data changes.

    Before revalidation, readiness is purely passengers + contact.
    Once a decisive revalidation existed, any material mutation keeps the
    stronger revalidation invalidation contract.
    """

    target_status, target_revalidation = (
        invalidate_after_material_mutation(
            booking.status,
            booking.revalidation_status,
        )
    )

    if (
        target_status == BookingStatus.REVALIDATION_REQUIRED
        or target_revalidation == RevalidationStatus.STALE
    ):
        return target_status, target_revalidation

    return (
        BookingStatus.READY_FOR_REVIEW
        if passengers_are_complete and contact_is_complete
        else BookingStatus.DRAFT,
        target_revalidation,
    )
