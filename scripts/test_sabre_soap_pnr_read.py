from __future__ import annotations

import argparse

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.sabre.soap_pnr_read import SabreSoapPnrReadService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import get_booking_repository


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Sabre SOAP CERT probe: open session, retrieve the "
            "persisted PNR and close session. No pricing/PQ/EndTransaction."
        )
    )
    parser.add_argument("booking_id")
    args = parser.parse_args()

    repository = get_booking_repository()
    booking = repository.get(args.booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {args.booking_id}")
    if booking.environment != "cert":
        raise SystemExit("SOAP CERT HARNESS REFUSAL: Booking no es CERT.")
    if booking.status != BookingStatus.PNR_CREATED:
        raise SystemExit(
            "SOAP CERT HARNESS REFUSAL: Booking no está PNR_CREATED."
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
            "SOAP CERT HARNESS REFUSAL: no hay PNR attempt SUCCEEDED."
        )

    revision = booking.accepted_offer_revision
    if revision is None:
        raise SystemExit("Booking sin oferta aceptada.")

    settings = get_settings("cert")
    if settings.sabre_env.strip().upper() != "CERT":
        raise SystemExit("SOAP CERT HARNESS REFUSAL: runtime no es CERT.")

    print("=== SABRE SOAP CERT READ ===")
    print(f"booking_id={booking.booking_id}")
    print(f"confirmation_id={attempt.confirmation_id}")
    print(f"selected_fare_currency={revision.snapshot.fare.currency}")
    print(f"selected_fare_total={revision.snapshot.fare.total_price}")
    print(f"soap_endpoint={settings.soap_endpoint}")
    print("mode=READ_ONLY")
    print("PII and SOAP session token omitted.")

    result = SabreSoapPnrReadService(settings).retrieve(
        attempt.confirmation_id
    )

    expected_segments = len(revision.snapshot.segments)
    print(f"pnr_retrieved={result.confirmation_id}")
    print(f"application_status={result.application_status}")
    print(f"flight_segment_count={result.flight_segment_count}")
    print(f"expected_flight_segment_count={expected_segments}")

    if result.flight_segment_count != expected_segments:
        raise SystemExit(
            "SOAP READ VALIDATION FAILED: Sabre no devolvió la misma "
            "cantidad de segmentos que el Booking congelado."
        )

    print("soap_read=success")
    print("soap_session=closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
