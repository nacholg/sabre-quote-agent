from __future__ import annotations

import re
from typing import Any, Callable

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.models.pnr_workspace import PnrSecureFlightDocsStatus
from app.models.quote_request import PassengerKind
from app.models.secure_flight import (
    SecureFlightDocumentInput,
    SecureFlightWriteReadiness,
    SecureFlightWriteReadinessStatus,
)
from app.sabre.soap_pnr_read import SabreSoapPnrReadService
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)
from app.services.pnr_secure_flight_docs_service import (
    assess_pnr_secure_flight_docs,
)


_TARGET_NAME_NUMBER = "1.1"


def _normalize_name_number(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.fullmatch(r"0*(\d+)\.0*(\d+)", raw)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2))}"
    return raw.upper()


def _normalize_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().upper().split())


class SecureFlightWriteReadinessService:
    """Fail-closed, read-only gate for a future Secure Flight DOCS write.

    v0.36.3 intentionally supports only the already-proven one-ADT /
    NameNumber 1.1 case. It performs a fresh independent TIR but never sends a
    Sabre mutation and never persists the transient document payload.
    """

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        attempt_service: BookingPnrAttemptService | None = None,
        passenger_service: BookingPassengerService | None = None,
        settings_loader: Callable[[str], Any] | None = None,
        reader_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )
        self.attempt_service = (
            attempt_service
            or BookingPnrAttemptService(
                booking_repository=self.booking_repository
            )
        )
        self.passenger_service = (
            passenger_service
            or BookingPassengerService(
                booking_repository=self.booking_repository
            )
        )
        self.settings_loader = settings_loader or get_settings
        self.reader_factory = (
            reader_factory
            or (lambda settings: SabreSoapPnrReadService(settings))
        )

    def assess(
        self,
        booking_id: str,
        *,
        document: SecureFlightDocumentInput,
    ) -> SecureFlightWriteReadiness:
        blockers: list[str] = []

        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)

        if booking.environment != "cert":
            blockers.append("SECURE_FLIGHT_CERT_ONLY")

        if booking.status != BookingStatus.PNR_CREATED:
            blockers.append("SECURE_FLIGHT_PNR_NOT_CREATED")

        attempt = self.attempt_service.get(booking_id)
        confirmation_id = (
            attempt.confirmation_id
            if (
                attempt is not None
                and attempt.status == PnrAttemptStatus.SUCCEEDED
                and attempt.confirmation_id
            )
            else None
        )
        if confirmation_id is None:
            blockers.append("SECURE_FLIGHT_PNR_ATTEMPT_NOT_SUCCEEDED")

        passengers = self.passenger_service.get(booking_id)
        if (
            len(passengers.passengers) != 1
            or passengers.passengers[0].passenger_type != PassengerKind.ADULT
        ):
            blockers.append("SECURE_FLIGHT_SINGLE_ADT_ONLY")

        passenger = (
            passengers.passengers[0]
            if len(passengers.passengers) == 1
            else None
        )
        if passenger is not None and not (
            passenger.given_name
            and passenger.surname
            and passenger.date_of_birth
            and passenger.gender
        ):
            blockers.append("SECURE_FLIGHT_PASSENGER_INCOMPLETE")

        revision = booking.accepted_offer_revision
        expected_segment_count = (
            len(revision.snapshot.segments)
            if revision is not None
            else None
        )
        if expected_segment_count is None:
            blockers.append("SECURE_FLIGHT_OFFER_REVISION_MISSING")

        # Runtime/write gates are evaluated before any remote read. This gate
        # remains read-only; it only reports whether a future write is allowed.
        try:
            settings = self.settings_loader(booking.environment)
        except Exception:
            blockers.append("SECURE_FLIGHT_RUNTIME_UNAVAILABLE")
            settings = None

        if settings is not None:
            if str(settings.sabre_env).strip().upper() != "CERT":
                blockers.append("SECURE_FLIGHT_RUNTIME_NOT_CERT")
            if not settings.sabre_secure_flight_enabled:
                blockers.append("SECURE_FLIGHT_WRITE_DISABLED")

        # Do not make a remote call when local invariants already fail.
        if blockers:
            return SecureFlightWriteReadiness(
                status=SecureFlightWriteReadinessStatus.BLOCKED,
                booking_id=booking.booking_id,
                confirmation_id=confirmation_id,
                target_name_number=None,
                fresh_remote_read=False,
                document=document.redacted_summary(),
                blockers=list(dict.fromkeys(blockers)),
            )

        assert settings is not None
        assert confirmation_id is not None
        assert passenger is not None
        assert expected_segment_count is not None

        try:
            result = self.reader_factory(settings).retrieve(confirmation_id)
        except Exception:
            return SecureFlightWriteReadiness(
                status=SecureFlightWriteReadinessStatus.BLOCKED,
                booking_id=booking.booking_id,
                confirmation_id=confirmation_id,
                target_name_number=None,
                fresh_remote_read=False,
                document=document.redacted_summary(),
                blockers=["SECURE_FLIGHT_FRESH_TIR_FAILED"],
            )

        snapshot = result.snapshot
        if (
            result.confirmation_id != confirmation_id
            or snapshot.confirmation_id != confirmation_id
        ):
            blockers.append("SECURE_FLIGHT_LOCATOR_MISMATCH")

        if len(snapshot.segments) != expected_segment_count:
            blockers.append("SECURE_FLIGHT_SEGMENT_COUNT_MISMATCH")

        if len(snapshot.passengers) != 1:
            blockers.append("SECURE_FLIGHT_TIR_SINGLE_PASSENGER_REQUIRED")
            tir_passenger = None
        else:
            tir_passenger = snapshot.passengers[0]

        target_name_number: str | None = None
        if tir_passenger is not None:
            target_name_number = _normalize_name_number(
                tir_passenger.name_number
            )
            if target_name_number != _TARGET_NAME_NUMBER:
                blockers.append("SECURE_FLIGHT_NAME_NUMBER_UNVERIFIED")

            expected_given = _normalize_name(
                " ".join(
                    value
                    for value in (
                        passenger.given_name,
                        passenger.middle_name,
                    )
                    if value
                )
            )
            actual_given = _normalize_name(tir_passenger.given_name)
            expected_surname = _normalize_name(passenger.surname)
            actual_surname = _normalize_name(tir_passenger.surname)

            if (
                not expected_given
                or not actual_given
                or expected_given != actual_given
                or not expected_surname
                or expected_surname != actual_surname
            ):
                blockers.append("SECURE_FLIGHT_PASSENGER_BINDING_MISMATCH")

            tir_type = str(tir_passenger.passenger_type or "").strip().upper()
            if tir_type and tir_type != PassengerKind.ADULT.value:
                blockers.append("SECURE_FLIGHT_PASSENGER_TYPE_MISMATCH")

        docs = assess_pnr_secure_flight_docs(snapshot)
        if docs.status == PnrSecureFlightDocsStatus.COMPLETE:
            if blockers:
                return SecureFlightWriteReadiness(
                    status=SecureFlightWriteReadinessStatus.BLOCKED,
                    booking_id=booking.booking_id,
                    confirmation_id=confirmation_id,
                    target_name_number=target_name_number,
                    fresh_remote_read=True,
                    document=document.redacted_summary(),
                    docs=docs,
                    blockers=list(dict.fromkeys(blockers)),
                )
            return SecureFlightWriteReadiness(
                status=SecureFlightWriteReadinessStatus.NOT_REQUIRED,
                booking_id=booking.booking_id,
                confirmation_id=confirmation_id,
                target_name_number=target_name_number,
                fresh_remote_read=True,
                document=document.redacted_summary(),
                docs=docs,
                blockers=[],
            )

        if docs.status != PnrSecureFlightDocsStatus.MISSING:
            blockers.append("SECURE_FLIGHT_DOCS_NOT_UNEQUIVOCALLY_MISSING")

        if (
            docs.passenger_count != 1
            or docs.missing_name_numbers != [_TARGET_NAME_NUMBER]
        ):
            blockers.append("SECURE_FLIGHT_DOCS_TARGET_UNVERIFIED")

        if blockers:
            return SecureFlightWriteReadiness(
                status=SecureFlightWriteReadinessStatus.BLOCKED,
                booking_id=booking.booking_id,
                confirmation_id=confirmation_id,
                target_name_number=target_name_number,
                fresh_remote_read=True,
                document=document.redacted_summary(),
                docs=docs,
                blockers=list(dict.fromkeys(blockers)),
            )

        return SecureFlightWriteReadiness(
            status=SecureFlightWriteReadinessStatus.READY,
            booking_id=booking.booking_id,
            confirmation_id=confirmation_id,
            target_name_number=_TARGET_NAME_NUMBER,
            fresh_remote_read=True,
            document=document.redacted_summary(),
            docs=docs,
            blockers=[],
        )
