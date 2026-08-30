from __future__ import annotations

import argparse

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.models.quote_request import PassengerKind
from app.sabre.soap_secure_flight import (
    SabreSoapSecureFlightReconciliationRequiredError,
    SabreSoapSecureFlightService,
)
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import get_booking_repository


def _load(booking_id: str):
    repository = get_booking_repository()
    booking = repository.get(booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {booking_id}")
    if booking.environment != "cert":
        raise SystemExit("SFPD CERT HARNESS REFUSAL: Booking no es CERT.")
    if booking.status != BookingStatus.PNR_CREATED:
        raise SystemExit(
            "SFPD CERT HARNESS REFUSAL: Booking no está PNR_CREATED."
        )

    attempt = BookingPnrAttemptService(
        booking_repository=repository
    ).get(booking.booking_id)
    if (
        attempt is None
        or attempt.status != PnrAttemptStatus.SUCCEEDED
        or not attempt.confirmation_id
    ):
        raise SystemExit(
            "SFPD CERT HARNESS REFUSAL: no hay PNR SUCCEEDED."
        )

    passengers = BookingPassengerService(
        booking_repository=repository
    ).get(booking.booking_id)
    if (
        len(passengers.passengers) != 1
        or passengers.passengers[0].passenger_type
        != PassengerKind.ADULT
    ):
        raise SystemExit(
            "SFPD CERT HARNESS REFUSAL: first write sólo permite 1 ADT."
        )

    passenger = passengers.passengers[0]
    if not (
        passenger.given_name
        and passenger.surname
        and passenger.date_of_birth
        and passenger.gender
    ):
        raise SystemExit(
            "SFPD CERT HARNESS REFUSAL: pasajero incompleto."
        )

    revision = booking.accepted_offer_revision
    if revision is None:
        raise SystemExit("Booking sin oferta aceptada.")

    return booking, attempt, passenger, revision


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or add minimum Secure Flight Passenger Data to an "
            "existing synthetic Sabre CERT PNR using PassengerDetailsRQ."
        )
    )
    parser.add_argument("booking_id")
    parser.add_argument(
        "--confirm-cert-write",
        action="store_true",
        help=(
            "Send PassengerDetailsRQ SecureFlight + EndTransaction in CERT."
        ),
    )
    args = parser.parse_args()

    booking, attempt, passenger, revision = _load(args.booking_id)
    expected_segments = len(revision.snapshot.segments)

    print("=== SABRE CERT SECURE FLIGHT PREVIEW ===")
    print(f"booking_id={booking.booking_id}")
    print(f"confirmation_id={attempt.confirmation_id}")
    print(f"expected_segment_count={expected_segments}")
    print("passenger_codes=ADT:1")
    print("secure_flight_fields=given_name,surname,date_of_birth,gender")
    print("document_number=not_requested")
    print("passport=not_requested")
    print("segment_scope=ALL")
    print("name_number=1.1")
    print("workflow=retrieve -> PassengerDetails SecureFlight -> EndTransaction")
    print("PII and SOAP session token omitted.")

    if not args.confirm_cert_write:
        print()
        print(
            "PREVIEW ONLY - no Secure Flight data or EndTransaction was sent."
        )
        print(
            "Actual CERT write requires SABRE_SECURE_FLIGHT_ENABLED=true "
            "and --confirm-cert-write."
        )
        return 0

    settings = get_settings("cert")
    if settings.sabre_env.strip().upper() != "CERT":
        raise SystemExit(
            "SFPD CERT HARNESS REFUSAL: runtime no es CERT."
        )
    if not settings.sabre_secure_flight_enabled:
        raise SystemExit(
            "SFPD CERT HARNESS REFUSAL: "
            "SABRE_SECURE_FLIGHT_ENABLED debe ser true."
        )

    given = " ".join(
        value.strip()
        for value in (
            passenger.given_name,
            passenger.middle_name,
        )
        if value and value.strip()
    )

    try:
        result = SabreSoapSecureFlightService(settings).store(
            attempt.confirmation_id,
            given_name=given,
            surname=passenger.surname,
            date_of_birth=passenger.date_of_birth.isoformat(),
            gender=passenger.gender,
            expected_segment_count=expected_segments,
        )
    except SabreSoapSecureFlightReconciliationRequiredError as exc:
        print()
        print("RESULT=RECONCILIATION_REQUIRED")
        print(str(exc))
        print("NO RETRY. Retrieve the locator and inspect *P3D.")
        return 3

    print()
    print("=== SABRE CERT SECURE FLIGHT RESULT ===")
    print(f"application_status={result.application_status}")
    print(f"flight_segment_count={result.flight_segment_count}")
    print(f"session_close_ok={str(result.session_close_ok).lower()}")
    print("SECURE FLIGHT WRITE COMPLETED IN CERT.")
    print("Manual verification required: retrieve locator and run *P3D.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
