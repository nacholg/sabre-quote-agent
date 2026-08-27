from __future__ import annotations

import argparse
import asyncio
from uuid import UUID, uuid4

from app.config import get_settings
from app.models.booking import (
    BookingCreatePnrRequest,
    BookingStatus,
    PnrAttemptStatus,
)
from app.sabre.create_booking import SabreCreateBookingProvider
from app.services.booking_create_pnr_builder import (
    BookingCreatePnrPayloadBuilder,
)
from app.services.booking_create_pnr_readiness_service import (
    BookingCreatePnrReadinessService,
)
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_pnr_execution_service import (
    BookingPnrExecutionReconciliationRequiredError,
    BookingPnrExecutionService,
)
from app.services.booking_repository import get_booking_repository


def _flight_summary(booking) -> list[str]:
    revision = booking.accepted_offer_revision
    if revision is None:
        return []
    result = []
    for segment in revision.snapshot.segments:
        result.append(
            f"{segment.marketing_carrier}{segment.flight_number} "
            f"{segment.departure_airport}-{segment.arrival_airport} "
            f"{str(segment.departure_at)[:16]} "
            f"class={segment.booking_class or '-'}"
        )
    return result


def _preview(booking_id: str) -> tuple[object, str]:
    repository = get_booking_repository()
    booking = repository.get(booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {booking_id}")

    if booking.environment != "cert":
        raise SystemExit(
            "CERT HARNESS REFUSAL: el Booking no pertenece a CERT."
        )

    if booking.status != BookingStatus.READY_TO_CREATE_PNR:
        raise SystemExit(
            "CERT HARNESS REFUSAL: el Booking no está "
            f"READY_TO_CREATE_PNR ({booking.status.value})."
        )

    readiness = BookingCreatePnrReadinessService(
        booking_repository=repository
    ).get(booking_id)
    if not readiness.ready:
        raise SystemExit(
            "CERT HARNESS REFUSAL: readiness gate falló: "
            + ", ".join(readiness.reasons)
        )

    _, fingerprint = BookingCreatePnrPayloadBuilder(
        booking_repository=repository
    ).build_with_fingerprint(booking_id)

    print("=== CREATE BOOKING CERT PREVIEW ===")
    print(f"booking_id={booking.booking_id}")
    print(f"revision={booking.revision}")
    print(f"environment={booking.environment}")
    print(f"passenger_count={readiness.passenger_count}")
    print(
        "passenger_codes="
        + ",".join(readiness.sabre_passenger_codes)
    )
    for flight in _flight_summary(booking):
        print(f"flight={flight}")
    print(f"request_fingerprint={fingerprint}")
    if readiness.warnings:
        print("warnings=" + ",".join(readiness.warnings))
    print("PII omitted from preview.")

    return booking, fingerprint


def _cert_settings_for_write():
    settings = get_settings("cert")

    if settings.sabre_env.strip().upper() != "CERT":
        raise SystemExit(
            "CERT HARNESS REFUSAL: runtime SABRE_ENV no es CERT."
        )

    if not settings.sabre_create_booking_enabled:
        raise SystemExit(
            "CERT HARNESS REFUSAL: SABRE_CREATE_BOOKING_ENABLED "
            "debe ser true para este proceso."
        )

    if settings.sabre_create_booking_prod_enabled:
        raise SystemExit(
            "CERT HARNESS REFUSAL: "
            "SABRE_CREATE_BOOKING_PROD_ENABLED debe permanecer false."
        )

    return settings


async def _execute(
    booking_id: str,
    client_request_id: UUID,
) -> int:
    repository = get_booking_repository()
    booking = repository.get(booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {booking_id}")

    settings = _cert_settings_for_write()
    provider = SabreCreateBookingProvider(settings=settings)
    service = BookingPnrExecutionService(
        booking_repository=repository,
        provider=provider,
    )

    request = BookingCreatePnrRequest(
        revision=booking.revision,
        client_request_id=client_request_id,
    )

    try:
        attempt = await service.execute(booking_id, request)
    except BookingPnrExecutionReconciliationRequiredError as exc:
        attempt = BookingPnrAttemptService(
            booking_repository=repository
        ).get(booking_id)
        print()
        print("RESULT=RECONCILIATION_REQUIRED")
        if attempt is not None:
            print(f"attempt_id={attempt.pnr_attempt_id}")
            print(f"status={attempt.status.value}")
            print(
                f"request_fingerprint="
                f"{attempt.request_fingerprint or '-'}"
            )
            print(f"error_code={attempt.error_code or '-'}")
        print("NO RETRY. Reconcile this attempt before any new action.")
        print(f"detail={str(exc)[:300]}")
        return 3

    print()
    print("=== CREATE BOOKING CERT RESULT ===")
    print(f"attempt_id={attempt.pnr_attempt_id}")
    print(f"status={attempt.status.value}")
    print(f"request_fingerprint={attempt.request_fingerprint or '-'}")
    print(f"confirmation_id={attempt.confirmation_id or '-'}")
    print(f"provider_reference={attempt.provider_reference or '-'}")
    print(f"error_code={attempt.error_code or '-'}")

    if attempt.status == PnrAttemptStatus.SUCCEEDED:
        print("PNR CREATED IN CERT.")
        return 0

    if attempt.status == PnrAttemptStatus.FAILED_SAFE:
        print(
            "FAILED_SAFE: provider definitively rejected/not-sent. "
            "Do not change client_request_id for a later explicit retry."
        )
        return 2

    print("Unexpected terminal state; inspect the persisted attempt.")
    return 4


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or explicitly execute one persisted Sabre Create "
            "Booking write in CERT. PII is never printed."
        )
    )
    parser.add_argument("booking_id")
    parser.add_argument(
        "--client-request-id",
        help=(
            "UUID used as the persistent Create PNR idempotency key. "
            "Required for an actual write."
        ),
    )
    parser.add_argument(
        "--confirm-cert-write",
        action="store_true",
        help="Actually send Create Booking to Sabre CERT.",
    )
    args = parser.parse_args()

    booking, _ = _preview(args.booking_id)

    if not args.confirm_cert_write:
        suggested = uuid4()
        print()
        print("PREVIEW ONLY - no request was sent to Sabre Create Booking.")
        print(
            "For the actual CERT write, reuse one explicit UUID, e.g.:"
        )
        print(
            "python scripts/create_pnr_cert.py "
            f"{booking.booking_id} "
            f"--client-request-id {suggested} "
            "--confirm-cert-write"
        )
        return 0

    if not args.client_request_id:
        raise SystemExit(
            "--client-request-id is mandatory with --confirm-cert-write."
        )

    try:
        request_id = UUID(args.client_request_id)
    except ValueError as exc:
        raise SystemExit(
            "--client-request-id must be a valid UUID."
        ) from exc

    return asyncio.run(
        _execute(booking.booking_id, request_id)
    )


if __name__ == "__main__":
    raise SystemExit(main())
