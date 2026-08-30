from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.models.quote_request import PassengerKind
from app.sabre.soap_air_price import SabreSoapAirPriceService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import get_booking_repository


PASSENGER_CODE = {
    PassengerKind.ADULT: "ADT",
    PassengerKind.CHILD: "CNN",
    PassengerKind.INFANT: "INF",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Quote an existing CERT PNR in the exact frozen Booking currency "
            "using OTA_AirPriceRQ. This probe does NOT Retain a PQ and does "
            "NOT EndTransaction."
        )
    )
    parser.add_argument("booking_id")
    args = parser.parse_args()

    repository = get_booking_repository()
    booking = repository.get(args.booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {args.booking_id}")
    if booking.environment != "cert":
        raise SystemExit("AIR PRICE CERT HARNESS REFUSAL: Booking no es CERT.")
    if booking.status != BookingStatus.PNR_CREATED:
        raise SystemExit(
            "AIR PRICE CERT HARNESS REFUSAL: Booking no está PNR_CREATED."
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
            "AIR PRICE CERT HARNESS REFUSAL: no hay PNR SUCCEEDED."
        )

    revision = booking.accepted_offer_revision
    if revision is None:
        raise SystemExit("Booking sin oferta aceptada.")

    fare = revision.snapshot.fare
    currency = fare.currency.strip().upper()
    expected_total = Decimal(str(fare.total_price))

    counts: Counter[str] = Counter()
    for spec in revision.snapshot.passenger_mix:
        counts[PASSENGER_CODE[spec.type]] += spec.quantity

    settings = get_settings("cert")
    if settings.sabre_env.strip().upper() != "CERT":
        raise SystemExit("AIR PRICE CERT HARNESS REFUSAL: runtime no es CERT.")

    print("=== SABRE SOAP CERT AIR PRICE PREVIEW ===")
    print(f"booking_id={booking.booking_id}")
    print(f"confirmation_id={attempt.confirmation_id}")
    print(f"requested_currency={currency}")
    print(f"expected_total={expected_total}")
    print(
        "passenger_codes="
        + ",".join(f"{code}:{qty}" for code, qty in sorted(counts.items()))
    )
    print("retain=false")
    print("end_transaction=false")
    print("mode=NON_PERSISTING_PRICE_CHECK")
    print("PII and SOAP session token omitted.")

    result = SabreSoapAirPriceService(settings).quote(
        attempt.confirmation_id,
        currency=currency,
        passenger_counts=dict(counts),
    )

    print()
    print("=== SABRE SOAP CERT AIR PRICE RESULT ===")
    print(f"application_status={result.application_status}")
    print(f"flight_segment_count={result.flight_segment_count}")
    print(f"result_currency={result.currency}")
    print(f"result_total={result.total}")
    print(f"expected_total={expected_total}")
    print(f"host_command={result.host_command or '-'}")

    if result.currency != currency:
        raise SystemExit(
            f"CURRENCY_MISMATCH: expected {currency}, got {result.currency}."
        )
    if result.total != expected_total:
        raise SystemExit(
            f"PRICE_MISMATCH: expected {expected_total}, got {result.total}."
        )

    print("pricing_match=true")
    print("NO PQ WAS RETAINED. NO END TRANSACTION WAS SENT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
