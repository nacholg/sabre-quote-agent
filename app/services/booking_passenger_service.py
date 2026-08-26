from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    BookingPassengerRow,
    BookingRevalidationRow,
    BookingRow,
)
from app.models.booking import (
    BookingPassengerIdentityUpdate,
    BookingPassengerRecord,
    BookingPassengersResponse,
    BookingPassengersUpdateRequest,
    BookingRecord,
    BookingStatus,
    RevalidationStatus,
)
from app.models.quote_request import PassengerKind
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)
from app.services.booking_state import invalidate_after_material_mutation


BOOKING_TABLE = BookingRow.__table__
PASSENGER_TABLE = BookingPassengerRow.__table__
REVALIDATION_TABLE = BookingRevalidationRow.__table__


class BookingPassengerValidationError(ValueError):
    """Passenger payload does not match the immutable priced passenger mix."""


class BookingRevisionConflictError(RuntimeError):
    """The Booking changed after the caller loaded it."""


class BookingPassengerLockedError(RuntimeError):
    """Passenger identities cannot be edited in a terminal Booking."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.strip().split()).upper()
    return cleaned or None


def _age_on(date_of_birth: date, travel_date: date) -> int:
    return (
        travel_date.year
        - date_of_birth.year
        - (
            (travel_date.month, travel_date.day)
            < (date_of_birth.month, date_of_birth.day)
        )
    )


class BookingPassengerService:
    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )

    def _booking(self, booking_id: str) -> BookingRecord:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)
        return booking

    @staticmethod
    def _expected_slots(
        booking: BookingRecord,
    ) -> list[dict[str, object]]:
        revision = booking.accepted_offer_revision
        if revision is None:
            raise BookingPassengerValidationError(
                "El Booking no tiene una revisión de oferta aceptada."
            )

        slots: list[dict[str, object]] = []
        slot_index = 1
        for spec in revision.snapshot.passenger_mix:
            for _ in range(spec.quantity):
                slots.append(
                    {
                        "slot_index": slot_index,
                        "passenger_type": spec.type.value,
                        "quoted_age": spec.age,
                    }
                )
                slot_index += 1

        if not slots:
            raise BookingPassengerValidationError(
                "El Booking no tiene pasajeros tarifados."
            )

        adult_slots = [
            int(slot["slot_index"])
            for slot in slots
            if slot["passenger_type"] == PassengerKind.ADULT.value
        ]
        if len(adult_slots) == 1:
            for slot in slots:
                if slot["passenger_type"] == PassengerKind.INFANT.value:
                    slot["associated_adult_slot_index"] = adult_slots[0]

        return slots

    def _passenger_rows(self, booking_id: str):
        with self.booking_repository.engine.connect() as connection:
            return (
                connection.execute(
                    select(PASSENGER_TABLE)
                    .where(PASSENGER_TABLE.c.booking_id == booking_id)
                    .order_by(PASSENGER_TABLE.c.slot_index)
                )
                .mappings()
                .all()
            )

    def _ensure_slots(self, booking: BookingRecord):
        existing = self._passenger_rows(booking.booking_id)
        if existing:
            return existing

        expected = self._expected_slots(booking)
        now = _utc_now()

        try:
            with self.booking_repository.engine.begin() as connection:
                for slot in expected:
                    connection.execute(
                        insert(PASSENGER_TABLE).values(
                            booking_id=booking.booking_id,
                            slot_index=slot["slot_index"],
                            passenger_type=slot["passenger_type"],
                            quoted_age=slot.get("quoted_age"),
                            given_name=None,
                            middle_name=None,
                            surname=None,
                            date_of_birth=None,
                            gender=None,
                            associated_adult_slot_index=slot.get(
                                "associated_adult_slot_index"
                            ),
                            updated_at=now,
                        )
                    )
        except IntegrityError:
            # Concurrent GETs may materialize the same immutable slots.
            pass

        return self._passenger_rows(booking.booking_id)

    @staticmethod
    def _travel_date(booking: BookingRecord) -> date:
        revision = booking.accepted_offer_revision
        if revision is None or not revision.snapshot.segments:
            raise BookingPassengerValidationError(
                "El Booking no tiene segmentos para validar edades."
            )

        raw = str(revision.snapshot.segments[0].departure_at)
        try:
            return date.fromisoformat(raw[:10])
        except ValueError as exc:
            raise BookingPassengerValidationError(
                "La fecha de viaje del Booking es inválida."
            ) from exc

    @staticmethod
    def _identity_from_update(
        item: BookingPassengerIdentityUpdate,
    ) -> dict[str, object]:
        return {
            "slot_index": item.slot_index,
            "given_name": _clean_name(item.given_name),
            "middle_name": _clean_name(item.middle_name),
            "surname": _clean_name(item.surname),
            "date_of_birth": (
                item.date_of_birth.isoformat()
                if item.date_of_birth is not None
                else None
            ),
            "gender": item.gender,
            "associated_adult_slot_index": item.associated_adult_slot_index,
        }

    @staticmethod
    def _identity_from_row(row) -> dict[str, object]:
        return {
            "slot_index": int(row["slot_index"]),
            "given_name": row["given_name"],
            "middle_name": row["middle_name"],
            "surname": row["surname"],
            "date_of_birth": row["date_of_birth"],
            "gender": row["gender"],
            "associated_adult_slot_index": row[
                "associated_adult_slot_index"
            ],
        }

    @staticmethod
    def _is_complete(
        identity: dict[str, object],
        *,
        passenger_type: str,
        adult_count: int,
    ) -> bool:
        basic = bool(
            identity.get("given_name")
            and identity.get("surname")
            and identity.get("date_of_birth")
        )
        if not basic:
            return False

        if (
            passenger_type == PassengerKind.INFANT.value
            and adult_count > 1
        ):
            return identity.get("associated_adult_slot_index") is not None

        return True

    def _validate_identity(
        self,
        identity: dict[str, object],
        row,
        *,
        travel_date: date,
        adult_slots: set[int],
    ) -> dict[str, object]:
        passenger_type = str(row["passenger_type"])
        quoted_age = row["quoted_age"]

        associated = identity.get("associated_adult_slot_index")
        if passenger_type != PassengerKind.INFANT.value:
            if associated is not None:
                raise BookingPassengerValidationError(
                    f"El pasajero {row['slot_index']} no es INF y no puede "
                    "asociarse a un adulto."
                )
        else:
            if associated is None and len(adult_slots) == 1:
                identity["associated_adult_slot_index"] = next(
                    iter(adult_slots)
                )
                associated = identity["associated_adult_slot_index"]

            if associated is not None and int(associated) not in adult_slots:
                raise BookingPassengerValidationError(
                    f"El adulto asociado al INF {row['slot_index']} "
                    "no corresponde a un slot ADT del Booking."
                )

        dob_raw = identity.get("date_of_birth")
        if dob_raw is None:
            return identity

        dob = date.fromisoformat(str(dob_raw))
        if dob > date.today():
            raise BookingPassengerValidationError(
                f"La fecha de nacimiento del pasajero {row['slot_index']} "
                "no puede estar en el futuro."
            )
        if dob > travel_date:
            raise BookingPassengerValidationError(
                f"La fecha de nacimiento del pasajero {row['slot_index']} "
                "es posterior al viaje."
            )

        age = _age_on(dob, travel_date)
        if passenger_type == PassengerKind.ADULT.value and age < 12:
            raise BookingPassengerValidationError(
                f"El pasajero {row['slot_index']} fue tarifado como ADT "
                "pero tendrá menos de 12 años al viajar."
            )
        if passenger_type == PassengerKind.CHILD.value:
            if not 2 <= age <= 11:
                raise BookingPassengerValidationError(
                    f"El pasajero {row['slot_index']} fue tarifado como "
                    "CHILD pero su edad al viajar no está entre 2 y 11 años."
                )
            if quoted_age is not None and age != int(quoted_age):
                raise BookingPassengerValidationError(
                    f"El pasajero {row['slot_index']} fue cotizado con "
                    f"{quoted_age} años y la fecha de nacimiento informa "
                    f"{age} años al viajar."
                )
        if passenger_type == PassengerKind.INFANT.value and age >= 2:
            raise BookingPassengerValidationError(
                f"El pasajero {row['slot_index']} fue tarifado como INF "
                "pero tendrá 2 años o más al viajar."
            )

        return identity

    @staticmethod
    def _response(
        booking: BookingRecord,
        rows,
    ) -> BookingPassengersResponse:
        adult_count = sum(
            1
            for row in rows
            if row["passenger_type"] == PassengerKind.ADULT.value
        )
        passengers: list[BookingPassengerRecord] = []

        for row in rows:
            identity = BookingPassengerService._identity_from_row(row)
            passenger_type = str(row["passenger_type"])
            passengers.append(
                BookingPassengerRecord(
                    slot_index=int(row["slot_index"]),
                    passenger_type=passenger_type,
                    quoted_age=row["quoted_age"],
                    given_name=row["given_name"],
                    middle_name=row["middle_name"],
                    surname=row["surname"],
                    date_of_birth=row["date_of_birth"],
                    gender=row["gender"],
                    associated_adult_slot_index=row[
                        "associated_adult_slot_index"
                    ],
                    complete=BookingPassengerService._is_complete(
                        identity,
                        passenger_type=passenger_type,
                        adult_count=adult_count,
                    ),
                )
            )

        return BookingPassengersResponse(
            booking_id=booking.booking_id,
            booking_revision=booking.revision,
            complete=bool(passengers)
            and all(item.complete for item in passengers),
            passengers=passengers,
        )

    def get(self, booking_id: str) -> BookingPassengersResponse:
        booking = self._booking(booking_id)
        rows = self._ensure_slots(booking)
        return self._response(booking, rows)

    def update(
        self,
        booking_id: str,
        request: BookingPassengersUpdateRequest,
    ) -> BookingPassengersResponse:
        booking = self._booking(booking_id)
        if booking.status in {
            BookingStatus.ABANDONED,
            BookingStatus.PNR_CREATED,
        }:
            raise BookingPassengerLockedError(
                f"No se pueden editar pasajeros con Booking "
                f"{booking.status.value}."
            )

        rows = self._ensure_slots(booking)
        expected_indices = {
            int(row["slot_index"])
            for row in rows
        }
        supplied_indices = [
            item.slot_index
            for item in request.passengers
        ]

        if len(set(supplied_indices)) != len(supplied_indices):
            raise BookingPassengerValidationError(
                "No se puede repetir slot_index en pasajeros."
            )
        if set(supplied_indices) != expected_indices:
            raise BookingPassengerValidationError(
                "La lista de pasajeros debe contener exactamente los slots "
                "fijados por la cotización."
            )

        travel_date = self._travel_date(booking)
        adult_slots = {
            int(row["slot_index"])
            for row in rows
            if row["passenger_type"] == PassengerKind.ADULT.value
        }
        adult_count = len(adult_slots)
        rows_by_slot = {
            int(row["slot_index"]): row
            for row in rows
        }

        incoming: dict[int, dict[str, object]] = {}
        for item in request.passengers:
            identity = self._identity_from_update(item)
            incoming[item.slot_index] = self._validate_identity(
                identity,
                rows_by_slot[item.slot_index],
                travel_date=travel_date,
                adult_slots=adult_slots,
            )

        unchanged = all(
            incoming[index] == self._identity_from_row(rows_by_slot[index])
            for index in expected_indices
        )
        if unchanged:
            # Safe network retry: the same already-persisted payload succeeds
            # even when the caller still carries the previous revision.
            return self._response(booking, rows)

        if request.revision != booking.revision:
            raise BookingRevisionConflictError(
                "El Booking cambió desde que abriste pasajeros. "
                f"Recargá antes de guardar (actual {booking.revision}, "
                f"recibida {request.revision})."
            )

        all_complete = all(
            self._is_complete(
                incoming[int(row["slot_index"])],
                passenger_type=str(row["passenger_type"]),
                adult_count=adult_count,
            )
            for row in rows
        )

        target_status, target_revalidation = (
            invalidate_after_material_mutation(
                booking.status,
                booking.revalidation_status,
            )
        )
        if (
            target_status == BookingStatus.READY_FOR_REVIEW
            and not all_complete
        ):
            target_status = BookingStatus.DRAFT

        now = _utc_now()
        next_revision = booking.revision + 1

        with self.booking_repository.engine.begin() as connection:
            for slot_index in sorted(expected_indices):
                values = incoming[slot_index]
                connection.execute(
                    update(PASSENGER_TABLE)
                    .where(
                        PASSENGER_TABLE.c.booking_id == booking_id,
                        PASSENGER_TABLE.c.slot_index == slot_index,
                    )
                    .values(
                        given_name=values["given_name"],
                        middle_name=values["middle_name"],
                        surname=values["surname"],
                        date_of_birth=values["date_of_birth"],
                        gender=values["gender"],
                        associated_adult_slot_index=values[
                            "associated_adult_slot_index"
                        ],
                        updated_at=now,
                    )
                )

            result = connection.execute(
                update(BOOKING_TABLE)
                .where(
                    BOOKING_TABLE.c.booking_id == booking_id,
                    BOOKING_TABLE.c.revision == request.revision,
                )
                .values(
                    status=target_status.value,
                    revalidation_status=target_revalidation.value,
                    revision=next_revision,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise BookingRevisionConflictError(
                    "El Booking fue modificado en paralelo. Recargá antes "
                    "de volver a guardar pasajeros."
                )

            if (
                target_revalidation == RevalidationStatus.STALE
                and booking.revalidation_status
                != RevalidationStatus.STALE
            ):
                connection.execute(
                    update(REVALIDATION_TABLE)
                    .where(
                        REVALIDATION_TABLE.c.booking_id == booking_id,
                        REVALIDATION_TABLE.c.stale_at.is_(None),
                    )
                    .values(stale_at=now)
                )

        updated_booking = self._booking(booking_id)
        updated_rows = self._passenger_rows(booking_id)
        return self._response(updated_booking, updated_rows)


def get_booking_passenger_service() -> BookingPassengerService:
    return BookingPassengerService()
