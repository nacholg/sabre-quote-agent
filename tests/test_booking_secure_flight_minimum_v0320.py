from __future__ import annotations

from app.services.booking_create_pnr_builder import (
    BookingCreatePnrPayloadError,
    sabre_create_booking_gender,
)
from app.services.booking_passenger_service import BookingPassengerService


def _identity(*, gender):
    return {
        "slot_index": 1,
        "given_name": "CERTTEST",
        "middle_name": None,
        "surname": "BOOKING",
        "date_of_birth": "1985-04-15",
        "gender": gender,
        "associated_adult_slot_index": None,
    }


def test_passenger_is_not_complete_without_gender() -> None:
    assert (
        BookingPassengerService._is_complete(
            _identity(gender=None),
            passenger_type="ADULT",
            adult_count=1,
        )
        is False
    )


def test_passenger_is_complete_with_minimum_secure_flight_fields() -> None:
    assert (
        BookingPassengerService._is_complete(
            _identity(gender="M"),
            passenger_type="ADULT",
            adult_count=1,
        )
        is True
    )


def test_create_booking_gender_mapping() -> None:
    assert sabre_create_booking_gender("M") == "MALE"
    assert sabre_create_booking_gender("F") == "FEMALE"
    assert sabre_create_booking_gender("X") == "UNDISCLOSED"


def test_create_booking_gender_mapping_rejects_missing_value() -> None:
    import pytest

    with pytest.raises(
        BookingCreatePnrPayloadError,
        match="Género requerido",
    ):
        sabre_create_booking_gender(None)
