from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.models.quote_request import PassengerKind
from app.sabre.soap_pq_store import (
    SabreSoapPqStoreReconciliationRequiredError,
    SabreSoapPqStoreService,
)
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import get_booking_repository


PASSENGER_CODE = {
    PassengerKind.ADULT: "ADT",
    PassengerKind.CHILD: "CNN",
    PassengerKind.INFANT: "INF",
}


def _load(booking_id: str):
    repository = get_booking_repository()
    booking = repository.get(booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {booking_id}")
    if booking.environment != "cert":
        raise SystemExit("PQ CERT HARNESS REFUSAL: Booking no es CERT.")
    if booking.status != BookingStatus.PNR_CREATED:
        raise SystemExit(
            "PQ CERT HARNESS REFUSAL: Booking no está PNR_CREATED."
        )

    attempt = BookingPnrAttemptService(
        booking_repository=repository
    ).get(booking.booking_id)
    if (
        attempt is None
        or attempt.status != PnrAttemptStatus.SUCCEEDED
        or not attempt.confirmation_id
    ):
        raise SystemExit("PQ CERT HARNESS REFUSAL: no hay PNR SUCCEEDED.")

    revision = booking.accepted_offer_revision
    if revision is None:
        raise SystemExit("Booking sin oferta aceptada.")

    counts: Counter[str] = Counter()
    total_pax = 0
    for spec in revision.snapshot.passenger_mix:
        counts[PASSENGER_CODE[spec.type]] += spec.quantity
        total_pax += spec.quantity

    # First retained-PQ write remains deliberately restricted to the same
    # synthetic shape already proven in CERT.
    if total_pax != 1 or counts != Counter({"ADT": 1}):
        raise SystemExit(
            "PQ CERT HARNESS REFUSAL: first write sólo permite 1 ADT."
        )

    return booking, attempt, revision, counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or explicitly retain one Sabre CERT PQ in the exact "
            "frozen Booking currency. EndTransaction is a non-idempotent write."
        )
    )
    parser.add_argument("booking_id")
    parser.add_argument(
        "--confirm-cert-write",
        action="store_true",
        help="Retain the PQ and send EndTransaction in Sabre CERT.",
    )
    args = parser.parse_args()

    booking, attempt, revision, counts = _load(args.booking_id)
    fare = revision.snapshot.fare
    currency = fare.currency.strip().upper()
    expected_total = Decimal(str(fare.total_price))
    expected_segments = len(revision.snapshot.segments)

    print("=== SABRE CERT STORE PQ PREVIEW ===")
    print(f"booking_id={booking.booking_id}")
    print(f"confirmation_id={attempt.confirmation_id}")
    print(f"currency={currency}")
    print(f"expected_total={expected_total}")
    print(f"expected_segment_count={expected_segments}")
    print("passenger_codes=ADT:1")
    print("workflow=retrieve -> price(no retain) -> price(retain) -> EndTransaction")
    print("received_from=SABRE QUOTE AGENT")
    print("PII and SOAP session token omitted.")

    if not args.confirm_cert_write:
        print()
        print("PREVIEW ONLY - no PQ was retained and no EndTransaction was sent.")
        print(
            "Actual CERT write requires SABRE_PNR_PRICING_ENABLED=true "
            "and --confirm-cert-write."
        )
        return 0

    settings = get_settings("cert")
    if settings.sabre_env.strip().upper() != "CERT":
        raise SystemExit("PQ CERT HARNESS REFUSAL: runtime no es CERT.")
    if not settings.sabre_pnr_pricing_enabled:
        raise SystemExit(
            "PQ CERT HARNESS REFUSAL: "
            "SABRE_PNR_PRICING_ENABLED debe ser true."
        )

    try:
        result = SabreSoapPqStoreService(settings).store(
            attempt.confirmation_id,
            currency=currency,
            passenger_counts=dict(counts),
            expected_total=expected_total,
            expected_segment_count=expected_segments,
        )
    except SabreSoapPqStoreReconciliationRequiredError as exc:
        print()
        print("RESULT=RECONCILIATION_REQUIRED")
        print(str(exc))
        print("NO RETRY. Verify *PQ before any new write.")
        return 3

    print()
    print("=== SABRE CERT STORE PQ RESULT ===")
    print(f"air_price_status={result.air_price_status}")
    print(f"end_transaction_status={result.end_transaction_status}")
    print(f"currency={result.currency}")
    print(f"total={result.total}")
    print(f"host_command={result.host_command or '-'}")
    print(f"flight_segment_count={result.flight_segment_count}")
    print(f"session_close_ok={str(result.session_close_ok).lower()}")
    print("PQ RETAIN + END TRANSACTION COMPLETED IN CERT.")
    print("Manual verification required: retrieve the locator and run *PQ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
