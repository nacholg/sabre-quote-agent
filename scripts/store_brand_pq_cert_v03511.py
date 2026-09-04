from __future__ import annotations

import argparse
from decimal import Decimal

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.models.quote_request import PassengerKind
from app.sabre.soap_brand_pq_store import (
    SabreBrandPqStoreError,
    SabreBrandPqStoreReconciliationRequiredError,
    SabreSoapBrandPqStoreService,
)
from app.sabre.soap_pnr_read import SabreSoapPnrReadService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import get_booking_repository
from app.services.pnr_pricing_selection_service import select_pnr_pricing


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
        raise SystemExit("BRAND PQ CERT HARNESS REFUSAL: Booking no es CERT.")
    if booking.status != BookingStatus.PNR_CREATED:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: Booking no está PNR_CREATED."
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
            "BRAND PQ CERT HARNESS REFUSAL: no hay PNR SUCCEEDED."
        )

    revision = booking.accepted_offer_revision
    if revision is None:
        raise SystemExit("Booking sin oferta aceptada.")

    if len(revision.snapshot.segments) != 1:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: v0.35.11b sólo permite 1 segmento."
        )
    if len(revision.snapshot.passenger_mix) != 1:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: v0.35.11b sólo permite 1 PTC."
        )
    pax_spec = revision.snapshot.passenger_mix[0]
    if pax_spec.quantity != 1 or pax_spec.type != PassengerKind.ADULT:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: v0.35.11b sólo permite 1 ADT."
        )

    return booking, attempt, revision


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or explicitly store one same-brand PQ in Sabre CERT. "
            "The write is non-idempotent and never auto-retries."
        )
    )
    parser.add_argument("booking_id")
    parser.add_argument("--brand", required=True)
    parser.add_argument("--expected-total", required=True)
    parser.add_argument(
        "--confirm-cert-write",
        action="store_true",
        help="Send price-by-brand + RQ and EndTransaction in Sabre CERT.",
    )
    args = parser.parse_args()

    booking, attempt, revision = _load(args.booking_id)
    settings = get_settings("cert")

    if settings.sabre_env.strip().upper() != "CERT":
        raise SystemExit("BRAND PQ CERT HARNESS REFUSAL: runtime no es CERT.")
    if settings.sabre_create_booking_enabled:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: CREATE_BOOKING debe estar False."
        )
    if settings.sabre_secure_flight_enabled:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: SECURE_FLIGHT debe estar False."
        )

    read = SabreSoapPnrReadService(settings).retrieve(
        attempt.confirmation_id
    )
    if len(read.snapshot.passengers) != 1:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: PNR no tiene exactamente 1 pasajero."
        )
    name_number = read.snapshot.passengers[0].name_number
    if not name_number:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: NameNumber no verificable."
        )

    currency = revision.snapshot.fare.currency.strip().upper()
    expected_total = Decimal(args.expected_total)
    expected_carrier = (
        revision.snapshot.fare.validating_carrier or ""
    ).strip().upper() or None

    service = SabreSoapBrandPqStoreService(settings)
    preview = service.preview(
        attempt.confirmation_id,
        currency=currency,
        brand_code=args.brand,
        segment_numbers=[1],
        name_number=name_number,
        passenger_code="ADT",
        expected_segment_count=1,
    )

    print("=== SABRE CERT SAME-BRAND PQ PREVIEW ===")
    print(f"booking_id={booking.booking_id}")
    print(f"confirmation_id={attempt.confirmation_id}")
    print(f"brand={args.brand.strip().upper()}")
    print(f"currency={preview.currency}")
    print(f"total={preview.total}")
    print(f"fare_basis={preview.fare_basis or '-'}")
    print(f"validating_carrier={preview.validating_carrier or '-'}")
    print(
        "last_day_to_purchase="
        f"{preview.last_day_to_purchase_raw or '-'}"
    )
    print(f"expected_total={expected_total}")
    print(f"price_matches={str(preview.total == expected_total).lower()}")
    print("retain=false")
    print("end_transaction=false")

    if preview.total != expected_total:
        print()
        print("REFUSAL: preview total differs from expected same-brand requote.")
        return 2
    if expected_carrier and preview.validating_carrier != expected_carrier:
        print()
        print("REFUSAL: validating carrier differs from frozen Booking.")
        return 2

    if not args.confirm_cert_write:
        print()
        print("PREVIEW ONLY - no PQ retained and no EndTransaction sent.")
        print(
            "For the explicit CERT write, enable "
            "SABRE_PNR_PRICING_ENABLED=true and rerun with "
            "--confirm-cert-write."
        )
        return 0

    if not settings.sabre_pnr_pricing_enabled:
        raise SystemExit(
            "BRAND PQ CERT HARNESS REFUSAL: "
            "SABRE_PNR_PRICING_ENABLED debe ser true."
        )

    try:
        result = service.store(
            attempt.confirmation_id,
            currency=currency,
            brand_code=args.brand,
            segment_numbers=[1],
            name_number=name_number,
            passenger_code="ADT",
            expected_total=expected_total,
            expected_segment_count=1,
            expected_validating_carrier=expected_carrier,
        )
    except SabreBrandPqStoreReconciliationRequiredError as exc:
        print()
        print("RESULT=RECONCILIATION_REQUIRED")
        print(str(exc))
        print("NO RETRY. Re-read TIR/*PQ before any further write.")
        return 3
    except SabreBrandPqStoreError as exc:
        print()
        print("RESULT=FAILED_SAFE")
        print(str(exc))
        return 2

    print()
    print("=== SABRE CERT SAME-BRAND PQ STORE RESULT ===")
    print(f"preview_total={result.preview.total}")
    print(f"retained_total={result.retained.total}")
    print(f"retained_fare_basis={result.retained.fare_basis or '-'}")
    print(
        "retained_last_day_to_purchase="
        f"{result.retained.last_day_to_purchase_raw or '-'}"
    )
    print(f"end_transaction_status={result.end_transaction_status}")
    print(
        "session_close_ok="
        f"{str(result.session_close_ok).lower()}"
    )

    fresh = SabreSoapPnrReadService(settings).retrieve(
        attempt.confirmation_id
    )
    selection = select_pnr_pricing(fresh.snapshot)

    print()
    print("=== FRESH TIR READ-BACK ===")
    print(f"price_quote_count={len(fresh.snapshot.price_quotes)}")
    for index, pq in enumerate(fresh.snapshot.price_quotes, start=1):
        print()
        print(f"pq_{index}_record={pq.record_number or '-'}")
        print(f"pq_{index}_status={pq.status or '-'}")
        print(
            f"pq_{index}_itinerary_changed="
            f"{pq.itinerary_changed}"
        )
        print(
            f"pq_{index}_total="
            f"{pq.total_currency or '-'} {pq.total_amount}"
        )
        print(
            f"pq_{index}_purchase_deadline_raw="
            f"{pq.purchase_deadline_raw or '-'}"
        )
        print(
            f"pq_{index}_fare_basis="
            f"{'/'.join(pq.fare_basis_codes or []) or pq.fare_basis or '-'}"
        )

    print()
    print(
        "authoritative_pq_records="
        f"{','.join(selection.candidate_record_numbers) or '-'}"
    )
    print(
        "authoritative_pq_count="
        f"{selection.candidate_quote_count}"
    )
    print(f"selection_message={selection.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
