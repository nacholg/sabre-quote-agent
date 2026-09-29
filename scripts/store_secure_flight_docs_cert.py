from __future__ import annotations

import argparse
from getpass import getpass

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.models.pnr_workspace import PnrSecureFlightDocsStatus
from app.models.quote_request import PassengerKind
from app.sabre.soap_pnr_read import SabreSoapPnrReadService
from app.sabre.soap_secure_flight_docs import (
    SabreSoapSecureFlightDocsProofService,
    SabreSoapSecureFlightDocsReconciliationRequiredError,
)
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import get_booking_repository
from app.services.pnr_secure_flight_docs_service import (
    assess_pnr_secure_flight_docs,
)


_TARGET_NAME_NUMBER = "1.1"


def _load(booking_id: str):
    repository = get_booking_repository()
    booking = repository.get(booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {booking_id}")
    if booking.environment != "cert":
        raise SystemExit("DOCS CERT PROOF REFUSAL: Booking no es CERT.")
    if booking.status != BookingStatus.PNR_CREATED:
        raise SystemExit(
            "DOCS CERT PROOF REFUSAL: Booking no está PNR_CREATED."
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
            "DOCS CERT PROOF REFUSAL: no hay PNR SUCCEEDED."
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
            "DOCS CERT PROOF REFUSAL: v0.36.1 sólo permite 1 ADT."
        )

    passenger = passengers.passengers[0]
    if not (
        passenger.given_name
        and passenger.surname
        and passenger.date_of_birth
        and passenger.gender
    ):
        raise SystemExit(
            "DOCS CERT PROOF REFUSAL: pasajero incompleto."
        )

    revision = booking.accepted_offer_revision
    if revision is None:
        raise SystemExit("Booking sin oferta aceptada.")

    return booking, attempt, passenger, revision


def _fresh_docs_status(settings, confirmation_id: str):
    result = SabreSoapPnrReadService(settings).retrieve(confirmation_id)
    return result, assess_pnr_secure_flight_docs(result.snapshot)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "v0.36.1 CERT-only proof: add APIS/DOCS to one existing synthetic "
            "Sabre PNR and verify DOCS/HK via an independent fresh TIR."
        )
    )
    parser.add_argument("booking_id")
    parser.add_argument("--document-type", default="P")
    parser.add_argument("--issuing-country")
    parser.add_argument("--nationality")
    parser.add_argument("--expiry-date")
    parser.add_argument(
        "--confirm-cert-write",
        action="store_true",
        help=(
            "Send exactly one PassengerDetailsRQ AdvancePassenger write in CERT."
        ),
    )
    args = parser.parse_args()

    booking, attempt, passenger, revision = _load(args.booking_id)
    expected_segments = len(revision.snapshot.segments)

    print("=== SABRE CERT DOCS PROOF PREVIEW ===")
    print(f"booking_id={booking.booking_id}")
    print(f"confirmation_id={attempt.confirmation_id}")
    print(f"expected_segment_count={expected_segments}")
    print("passenger_codes=ADT:1")
    print(f"name_number={_TARGET_NAME_NUMBER}")
    print("document_number=never_logged; prompted only at confirmed write")
    print("workflow=fresh TIR -> one PassengerDetailsRQ -> EOT -> fresh TIR")
    print("PII document number and SOAP session token are never printed.")

    if not args.confirm_cert_write:
        print()
        print("PREVIEW ONLY - no mutation was sent.")
        return 0

    missing_args = [
        flag
        for flag, value in (
            ("--issuing-country", args.issuing_country),
            ("--nationality", args.nationality),
            ("--expiry-date", args.expiry_date),
        )
        if not str(value or "").strip()
    ]
    if missing_args:
        raise SystemExit(
            "DOCS CERT PROOF REFUSAL: faltan "
            + ", ".join(missing_args)
            + "."
        )

    settings = get_settings("cert")
    if settings.sabre_env.strip().upper() != "CERT":
        raise SystemExit(
            "DOCS CERT PROOF REFUSAL: runtime no es CERT."
        )
    if not settings.sabre_secure_flight_enabled:
        raise SystemExit(
            "DOCS CERT PROOF REFUSAL: "
            "SABRE_SECURE_FLIGHT_ENABLED debe ser true."
        )

    try:
        before, before_docs = _fresh_docs_status(
            settings,
            attempt.confirmation_id,
        )
    except Exception as exc:
        raise SystemExit(
            "DOCS CERT PROOF REFUSAL: fresh pre-write TIR falló."
        ) from exc

    if (
        before.confirmation_id != attempt.confirmation_id
        or before.flight_segment_count != expected_segments
    ):
        raise SystemExit(
            "DOCS CERT PROOF REFUSAL: locator/segments no coinciden."
        )

    if before_docs.status == PnrSecureFlightDocsStatus.COMPLETE:
        print()
        print("RESULT=NOT_REQUIRED")
        print("DOCS ya está COMPLETE/HK. Zero write.")
        return 0

    if before_docs.status != PnrSecureFlightDocsStatus.MISSING:
        print()
        print("RESULT=BLOCKED")
        print("DOCS pre-write no es inequívocamente MISSING. Zero write.")
        return 2

    if (
        before_docs.passenger_count != 1
        or before_docs.missing_name_numbers != [_TARGET_NAME_NUMBER]
    ):
        print()
        print("RESULT=BLOCKED")
        print("NameNumber objetivo no es inequívoco. Zero write.")
        return 2

    document_number = getpass(
        "Synthetic CERT document number (hidden, never logged): "
    ).strip()
    if not document_number:
        raise SystemExit(
            "DOCS CERT PROOF REFUSAL: document number vacío."
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
        result = SabreSoapSecureFlightDocsProofService(settings).store(
            attempt.confirmation_id,
            given_name=given,
            surname=passenger.surname,
            date_of_birth=passenger.date_of_birth.isoformat(),
            gender=passenger.gender,
            document_type=args.document_type,
            document_number=document_number,
            issuing_country=args.issuing_country,
            nationality=args.nationality,
            expiry_date=args.expiry_date,
            expected_segment_count=expected_segments,
            name_number=_TARGET_NAME_NUMBER,
        )
    except SabreSoapSecureFlightDocsReconciliationRequiredError as exc:
        print()
        print("RESULT=RECONCILIATION_REQUIRED")
        print(str(exc))
        print("NO RETRY.")
        return 3
    finally:
        document_number = ""

    print()
    print("PassengerDetailsRQ result received.")
    print(f"application_status={result.application_status}")
    print(f"flight_segment_count={result.flight_segment_count}")
    print(f"session_close_ok={str(result.session_close_ok).lower()}")
    print("Running independent fresh TIR verification...")

    try:
        after, after_docs = _fresh_docs_status(
            settings,
            attempt.confirmation_id,
        )
    except Exception:
        print()
        print("RESULT=RECONCILIATION_REQUIRED")
        print("Post-submit fresh TIR could not verify the mutation.")
        print("NO RETRY.")
        return 4

    if (
        after.confirmation_id != attempt.confirmation_id
        or after.flight_segment_count != expected_segments
        or after_docs.status != PnrSecureFlightDocsStatus.COMPLETE
        or _TARGET_NAME_NUMBER not in after_docs.covered_name_numbers
    ):
        print()
        print("RESULT=RECONCILIATION_REQUIRED")
        print("Fresh TIR did not prove DOCS/HK for NameNumber 1.1.")
        print("NO RETRY.")
        return 4

    print()
    print("RESULT=PROOF_SUCCEEDED")
    print("Fresh TIR confirms DOCS/HK for NameNumber 1.1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
