from __future__ import annotations

import argparse
from collections import Counter

from app.models.pnr_workspace import PnrSnapshot

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.sabre.soap_pnr_read import SabreSoapPnrReadService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import get_booking_repository


def _compact_counter(values: list[str]) -> str:
    counts = Counter(values)
    if not counts:
        return "-"
    return ",".join(f"{key}:{counts[key]}" for key in sorted(counts))


def _safe_snapshot_lines(snapshot: PnrSnapshot) -> list[str]:
    """Return a diagnostic summary without passenger/contact PII values."""

    lines = [
        f"snapshot_passenger_count={len(snapshot.passengers)}",
        "snapshot_passenger_types="
        + _compact_counter(
            [passenger.passenger_type or "UNKNOWN" for passenger in snapshot.passengers]
        ),
        f"snapshot_contact_count={len(snapshot.contacts)}",
        "snapshot_contact_kinds="
        + _compact_counter([contact.kind for contact in snapshot.contacts]),
        f"snapshot_price_quote_count={len(snapshot.price_quotes)}",
        "snapshot_pricing_state="
        + ("present" if snapshot.price_quotes else "missing"),
        f"snapshot_special_service_count={len(snapshot.special_services)}",
        "snapshot_ticketing_type=" + (snapshot.ticketing.ticket_type or "-"),
        "snapshot_ticketing_advisory="
        + (
            f"{snapshot.ticketing.advisory_airline_code or '-'}:"
            f"{snapshot.ticketing.advisory_code or '-'}:"
            f"{snapshot.ticketing.advisory_status or '-'}"
            if snapshot.ticketing.advisory_present
            else "-"
        ),
        "snapshot_ticketing_deadline=" + (snapshot.ticketing.deadline_at or "-"),
    ]

    for index, segment in enumerate(snapshot.segments, start=1):
        lines.append(
            f"snapshot_segment_{index}="
            f"carrier={segment.marketing_carrier or '-'} "
            f"flight={segment.flight_number or '-'} "
            f"route={segment.origin or '-'}-{segment.destination or '-'} "
            f"departure={segment.departure_at or '-'} "
            f"arrival={segment.arrival_at or '-'} "
            f"class={segment.booking_class or '-'} "
            f"status={segment.status or '-'} "
            f"party={segment.number_in_party if segment.number_in_party is not None else '-'}"
        )

    for index, price_quote in enumerate(snapshot.price_quotes, start=1):
        fare_basis = (
            ",".join(price_quote.fare_basis_codes)
            if price_quote.fare_basis_codes
            else (price_quote.fare_basis or "-")
        )
        booking_classes = (
            ",".join(price_quote.segment_booking_classes)
            if price_quote.segment_booking_classes
            else "-"
        )
        lines.append(
            f"snapshot_pq_{index}="
            f"record={price_quote.record_number or '-'} "
            f"status={price_quote.status or '-'} "
            f"pax_type={price_quote.passenger_type or '-'} "
            f"pax_qty={price_quote.passenger_quantity if price_quote.passenger_quantity is not None else '-'} "
            f"validating_carrier={price_quote.validating_carrier or '-'} "
            f"total_currency={price_quote.total_currency or '-'} "
            f"total_amount={price_quote.total_amount if price_quote.total_amount is not None else '-'} "
            f"fare_basis={fare_basis} "
            f"classes={booking_classes}"
        )

    if snapshot.special_services:
        lines.append(
            "snapshot_ssr_codes="
            + ",".join(
                sorted(
                    {
                        f"{service.airline_code or '-'}:{service.code}:{service.status or '-'}"
                        for service in snapshot.special_services
                    }
                )
            )
        )

    return lines


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

    print()
    print("=== NORMALIZED PNR SNAPSHOT (PII-SAFE) ===")
    for line in _safe_snapshot_lines(result.snapshot):
        print(line)

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
