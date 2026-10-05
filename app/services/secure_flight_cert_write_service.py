from __future__ import annotations

from typing import Any, Callable

from app.config import get_settings
from app.models.secure_flight import (
    SecureFlightCertWriteResult,
    SecureFlightCertWriteStatus,
    SecureFlightDocumentInput,
    SecureFlightWriteReadinessStatus,
)
from app.sabre.soap_secure_flight_docs import (
    SabreSoapSecureFlightDocsProofService,
    SabreSoapSecureFlightDocsReconciliationRequiredError,
)
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_repository import BookingRepository, get_booking_repository
from app.services.secure_flight_write_readiness_service import (
    SecureFlightWriteReadinessService,
)


class SecureFlightCertWriteService:
    """CERT-only orchestration for one Secure Flight DOCS mutation."""

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        passenger_service: BookingPassengerService | None = None,
        readiness_service: SecureFlightWriteReadinessService | None = None,
        settings_loader: Callable[[str], Any] | None = None,
        writer_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self.booking_repository = booking_repository or get_booking_repository()
        self.passenger_service = passenger_service or BookingPassengerService(
            booking_repository=self.booking_repository
        )
        self.settings_loader = settings_loader or get_settings
        self.readiness_service = readiness_service or SecureFlightWriteReadinessService(
            booking_repository=self.booking_repository,
            passenger_service=self.passenger_service,
            settings_loader=self.settings_loader,
        )
        self.writer_factory = writer_factory or (
            lambda settings: SabreSoapSecureFlightDocsProofService(settings)
        )

    def execute(
        self,
        booking_id: str,
        *,
        document: SecureFlightDocumentInput,
    ) -> SecureFlightCertWriteResult:
        pre = self.readiness_service.assess(booking_id, document=document)

        if pre.status == SecureFlightWriteReadinessStatus.NOT_REQUIRED:
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.NOT_REQUIRED,
                booking_id=pre.booking_id,
                confirmation_id=pre.confirmation_id,
                target_name_number=pre.target_name_number,
                write_submitted=False,
                document=pre.document,
                docs=pre.docs,
                message="DOCS/HK ya estaba completo. Zero write.",
            )

        if pre.status != SecureFlightWriteReadinessStatus.READY:
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.BLOCKED,
                booking_id=pre.booking_id,
                confirmation_id=pre.confirmation_id,
                target_name_number=pre.target_name_number,
                write_submitted=False,
                document=pre.document,
                docs=pre.docs,
                blockers=pre.blockers,
                message="Secure Flight write readiness bloqueó la mutación.",
            )

        if not pre.confirmation_id or not pre.target_name_number:
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.BLOCKED,
                booking_id=pre.booking_id,
                confirmation_id=pre.confirmation_id,
                target_name_number=pre.target_name_number,
                write_submitted=False,
                document=pre.document,
                docs=pre.docs,
                blockers=["SECURE_FLIGHT_READY_BINDING_INCOMPLETE"],
            )

        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)

        passengers = self.passenger_service.get(booking_id)
        if len(passengers.passengers) != 1:
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.BLOCKED,
                booking_id=booking_id,
                confirmation_id=pre.confirmation_id,
                target_name_number=pre.target_name_number,
                write_submitted=False,
                document=pre.document,
                docs=pre.docs,
                blockers=["SECURE_FLIGHT_PASSENGER_STATE_CHANGED"],
            )

        passenger = passengers.passengers[0]
        revision = booking.accepted_offer_revision
        if revision is None:
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.BLOCKED,
                booking_id=booking_id,
                confirmation_id=pre.confirmation_id,
                target_name_number=pre.target_name_number,
                write_submitted=False,
                document=pre.document,
                docs=pre.docs,
                blockers=["SECURE_FLIGHT_OFFER_REVISION_MISSING"],
            )

        given_name = " ".join(
            value.strip()
            for value in (passenger.given_name, passenger.middle_name)
            if value and value.strip()
        )
        expected_segment_count = len(revision.snapshot.segments)

        try:
            settings = self.settings_loader(booking.environment)
        except Exception:
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.BLOCKED,
                booking_id=booking_id,
                confirmation_id=pre.confirmation_id,
                target_name_number=pre.target_name_number,
                write_submitted=False,
                document=pre.document,
                docs=pre.docs,
                blockers=["SECURE_FLIGHT_RUNTIME_UNAVAILABLE"],
            )

        writer = self.writer_factory(settings)
        try:
            write_result = writer.store(
                pre.confirmation_id,
                given_name=given_name,
                surname=passenger.surname,
                date_of_birth=passenger.date_of_birth.isoformat(),
                gender=passenger.gender,
                document_type=document.document_type,
                document_number=document.document_number_value(),
                issuing_country=document.issuing_country,
                nationality=document.nationality,
                expiry_date=document.expiry_date.isoformat(),
                expected_segment_count=expected_segment_count,
                name_number=pre.target_name_number,
            )
        except SabreSoapSecureFlightDocsReconciliationRequiredError:
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.RECONCILIATION_REQUIRED,
                booking_id=booking_id,
                confirmation_id=pre.confirmation_id,
                target_name_number=pre.target_name_number,
                write_submitted=True,
                document=pre.document,
                docs=pre.docs,
                blockers=["SECURE_FLIGHT_WRITE_AMBIGUOUS"],
                message="Resultado ambiguo después de submit. NO RETRY.",
            )
        except Exception:
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.BLOCKED,
                booking_id=booking_id,
                confirmation_id=pre.confirmation_id,
                target_name_number=pre.target_name_number,
                write_submitted=False,
                document=pre.document,
                docs=pre.docs,
                blockers=["SECURE_FLIGHT_WRITE_NOT_SUBMITTED"],
            )

        post = self.readiness_service.assess(booking_id, document=document)

        if (
            post.status == SecureFlightWriteReadinessStatus.NOT_REQUIRED
            and post.fresh_remote_read
            and post.confirmation_id == pre.confirmation_id
            and post.target_name_number == pre.target_name_number
            and post.docs is not None
        ):
            return SecureFlightCertWriteResult(
                status=SecureFlightCertWriteStatus.SUCCEEDED,
                booking_id=booking_id,
                confirmation_id=post.confirmation_id,
                target_name_number=post.target_name_number,
                write_submitted=True,
                application_status=write_result.application_status,
                session_close_ok=write_result.session_close_ok,
                document=post.document,
                docs=post.docs,
                message="Fresh independent TIR confirmó DOCS/HK.",
            )

        return SecureFlightCertWriteResult(
            status=SecureFlightCertWriteStatus.RECONCILIATION_REQUIRED,
            booking_id=booking_id,
            confirmation_id=pre.confirmation_id,
            target_name_number=pre.target_name_number,
            write_submitted=True,
            application_status=write_result.application_status,
            session_close_ok=write_result.session_close_ok,
            document=pre.document,
            docs=post.docs,
            blockers=list(dict.fromkeys([
                "SECURE_FLIGHT_POST_WRITE_NOT_VERIFIED",
                *post.blockers,
            ])),
            message=(
                "PassengerDetailsRQ fue Complete pero el fresh TIR posterior "
                "no probó DOCS/HK. NO RETRY."
            ),
        )
