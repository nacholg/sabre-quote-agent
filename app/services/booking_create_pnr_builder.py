from __future__ import annotations

import hashlib
import json
from collections import Counter

from app.models.booking import BookingOfferSnapshot
from app.services.booking_contact_service import BookingContactService
from app.services.booking_create_pnr_readiness_service import (
    BookingCreatePnrReadinessService,
    sabre_create_booking_passenger_code,
)
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_repository import BookingRepository, get_booking_repository

_HALT_ON_FLIGHT_STATUS_CODES = ["NO", "UC", "US", "UN", "UU", "LL", "HL"]


class BookingCreatePnrPayloadError(RuntimeError):
    """Booking cannot yet be represented safely as Create Booking payload."""


def create_booking_payload_fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _departure_parts(value: str) -> tuple[str, str]:
    from datetime import datetime

    text = str(value or "").strip()
    if not text:
        raise BookingCreatePnrPayloadError(
            "DepartureDateTime vacío para Create Booking."
        )

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise BookingCreatePnrPayloadError(
            "DepartureDateTime inválido para Create Booking: "
            f"{text}."
        ) from exc

    return parsed.date().isoformat(), parsed.strftime("%H:%M")

def _traveler_given_name(given_name: str | None, middle_name: str | None) -> str:
    return " ".join(
        str(value).strip()
        for value in (given_name, middle_name)
        if value and str(value).strip()
    )


class BookingCreatePnrPayloadBuilder:
    """Pure dry-run Create Booking builder. No HTTP calls and no mutations."""

    def __init__(self, *, booking_repository: BookingRepository | None = None) -> None:
        self.booking_repository = booking_repository or get_booking_repository()
        self.readiness_service = BookingCreatePnrReadinessService(
            booking_repository=self.booking_repository
        )
        self.passenger_service = BookingPassengerService(
            booking_repository=self.booking_repository
        )
        self.contact_service = BookingContactService(
            booking_repository=self.booking_repository
        )

    @staticmethod
    def _flights(snapshot: BookingOfferSnapshot) -> list[dict[str, object]]:
        # Current Booking does not preserve Sabre marriage-group metadata.
        # Only direct O&Ds can safely use false without inventing data.
        if len(snapshot.segments) != len(snapshot.legs):
            raise BookingCreatePnrPayloadError(
                "El itinerario tiene una conexión pero Booking todavía no "
                "preserva isMarriageGroup. No se generará un payload inventado."
            )

        flights: list[dict[str, object]] = []
        for segment in snapshot.segments:
            departure_date, departure_time = _departure_parts(
                str(segment.departure_at)
            )
            flight_number = str(segment.flight_number or "").strip()
            if not flight_number.isdigit():
                raise BookingCreatePnrPayloadError(
                    f"Número de vuelo inválido: {flight_number or '<vacío>'}."
                )
            flights.append(
                {
                    "flightNumber": int(flight_number),
                    "airlineCode": str(segment.marketing_carrier or "").strip().upper(),
                    "fromAirportCode": str(segment.departure_airport or "").strip().upper(),
                    "toAirportCode": str(segment.arrival_airport or "").strip().upper(),
                    "departureDate": departure_date,
                    "departureTime": departure_time,
                    "bookingClass": str(segment.booking_class or "").strip().upper(),
                    "flightStatusCode": "NN",
                    "isMarriageGroup": False,
                }
            )
        return flights

    def build(self, booking_id: str) -> dict[str, object]:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)

        readiness = self.readiness_service.get(booking_id)
        if not readiness.ready:
            details = ", ".join(readiness.reasons) or "not_ready"
            raise BookingCreatePnrPayloadError(
                f"El Booking no está listo para Create Booking: {details}."
            )

        revision = booking.accepted_offer_revision
        if revision is None:
            raise BookingCreatePnrPayloadError("El Booking no tiene oferta aceptada.")
        snapshot = revision.snapshot

        passengers = self.passenger_service.get(booking_id)
        if not passengers.complete:
            raise BookingCreatePnrPayloadError("Los pasajeros están incompletos.")

        travelers: list[dict[str, object]] = []
        passenger_codes: Counter[str] = Counter()
        for passenger in passengers.passengers:
            code = sabre_create_booking_passenger_code(passenger.passenger_type)
            passenger_codes[code] += 1
            given_name = _traveler_given_name(
                passenger.given_name,
                passenger.middle_name,
            )
            if not given_name or not passenger.surname or not passenger.date_of_birth:
                raise BookingCreatePnrPayloadError(
                    f"Pasajero slot {passenger.slot_index} incompleto."
                )
            travelers.append(
                {
                    "givenName": given_name,
                    "surname": str(passenger.surname).strip(),
                    "birthDate": passenger.date_of_birth.isoformat(),
                    "passengerCode": code,
                }
            )

        contact = self.contact_service.get(booking_id)
        phone = (
            f"{contact.phone_country_code}{contact.phone_number}"
            if contact.phone_country_code and contact.phone_number
            else None
        )
        if not contact.complete or not contact.email or not phone:
            raise BookingCreatePnrPayloadError(
                "Create Booking requiere email y teléfono completos."
            )

        pricing: dict[str, object] = {
            "passengersPricing": [
                {
                    "passengerCode": code,
                    "forcePassengerCode": False,
                    "numberOfpassengers": count,
                }
                for code, count in passenger_codes.items()
            ]
        }
        validating = str(snapshot.fare.validating_carrier or "").strip().upper()
        if validating:
            pricing["validatingAirlineCode"] = validating

        return {
            "travelers": travelers,
            "contactInfo": {
                "emails": [contact.email],
                "phones": [phone],
            },
            "flightDetails": {
                "haltOnFlightStatusCodes": list(_HALT_ON_FLIGHT_STATUS_CODES),
                "flights": self._flights(snapshot),
                "flightPricing": [pricing],
            },
        }

    def build_with_fingerprint(
        self,
        booking_id: str,
    ) -> tuple[dict[str, object], str]:
        payload = self.build(booking_id)
        return payload, create_booking_payload_fingerprint(payload)
