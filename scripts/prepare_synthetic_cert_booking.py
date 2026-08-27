from __future__ import annotations

import argparse
import asyncio
from uuid import uuid4

from app.models.booking import (
    BookingContactUpdateRequest,
    BookingPassengersUpdateRequest,
    BookingRevalidationRequest,
    BookingStatus,
)
from app.models.quote_request import PassengerKind
from app.services.booking_contact_service import BookingContactService
from app.services.booking_passenger_service import BookingPassengerService
from app.services.booking_revalidation_service import BookingRevalidationService
from app.services.booking_repository import get_booking_repository


SYNTHETIC_PASSENGER = {
    "given_name": "CERTTEST",
    "surname": "BOOKING",
    "date_of_birth": "1985-04-15",
    "gender": "M",
}
SYNTHETIC_CONTACT = {
    "name": "CERT TEST BOOKING",
    "email": "test@example.com",
    "phone_country_code": "+54",
    "phone_number": "1100000000",
    "preferred_channel": "email",
}


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


async def _run(source_booking_id: str) -> int:
    repository = get_booking_repository()
    source = repository.get(source_booking_id)
    if source is None:
        raise SystemExit(f"Booking origen inexistente: {source_booking_id}")

    if source.environment != "cert":
        raise SystemExit(
            "El Booking origen no es CERT. No se clonará ningún producto PROD."
        )

    revision = source.accepted_offer_revision
    if revision is None:
        raise SystemExit("El Booking origen no tiene oferta aceptada.")

    mix = list(revision.snapshot.passenger_mix)
    if (
        len(mix) != 1
        or mix[0].type != PassengerKind.ADULT
        or mix[0].quantity != 1
    ):
        raise SystemExit(
            "El preparador del primer write sólo admite 1 ADT. "
            "Elegí un Booking CERT simple de un adulto."
        )

    clone = repository.create_initial(
        source_quote_id=source.source_quote_id,
        selected_rank=source.selected_rank,
        environment="cert",
        client_request_id=str(uuid4()),
        snapshot=revision.snapshot.model_copy(deep=True),
    )

    passenger_service = BookingPassengerService(
        booking_repository=repository
    )
    passenger_service.update(
        clone.booking_id,
        BookingPassengersUpdateRequest(
            revision=clone.revision,
            passengers=[
                {
                    "slot_index": 1,
                    **SYNTHETIC_PASSENGER,
                }
            ],
        ),
    )

    after_passenger = repository.get(clone.booking_id)
    if after_passenger is None:
        raise RuntimeError("No se pudo releer el Booking sintético.")

    contact_service = BookingContactService(
        booking_repository=repository
    )
    contact_service.update(
        clone.booking_id,
        BookingContactUpdateRequest(
            revision=after_passenger.revision,
            **SYNTHETIC_CONTACT,
        ),
    )

    before_revalidation = repository.get(clone.booking_id)
    if before_revalidation is None:
        raise RuntimeError("No se pudo releer el Booking antes de revalidar.")

    response = await BookingRevalidationService(
        booking_repository=repository
    ).revalidate(
        clone.booking_id,
        BookingRevalidationRequest(
            revision=before_revalidation.revision
        ),
    )

    final = repository.get(clone.booking_id)
    if final is None:
        raise RuntimeError("No se pudo releer el Booking revalidado.")

    print(f"booking_id={final.booking_id}")
    print(f"environment={final.environment}")
    print(f"revision={final.revision}")
    print(f"status={final.status.value}")
    print(f"revalidation_status={final.revalidation_status.value}")
    print(f"revalidation_id={response.revalidation_id}")
    for flight in _flight_summary(final):
        print(f"flight={flight}")

    if final.status != BookingStatus.READY_TO_CREATE_PNR:
        print()
        print(
            "STOP: Sabre revalidó, pero el Booking no quedó "
            "READY_TO_CREATE_PNR. No ejecutar Create Booking."
        )
        return 2

    print()
    print("Synthetic CERT Booking READY_TO_CREATE_PNR.")
    print("No PNR was created.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clone product-only data from a CERT Booking, attach synthetic "
            "PII, and revalidate it. No PNR is created."
        )
    )
    parser.add_argument("source_booking_id")
    args = parser.parse_args()
    return asyncio.run(_run(args.source_booking_id))


if __name__ == "__main__":
    raise SystemExit(main())
