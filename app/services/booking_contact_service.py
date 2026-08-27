from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    BookingContactRow,
    BookingRevalidationRow,
    BookingRow,
)
from app.models.booking import (
    BookingContactRecord,
    BookingContactUpdateRequest,
    BookingRecord,
    BookingStatus,
    RevalidationStatus,
)
from app.services.booking_readiness_service import (
    contact_values_complete,
    passengers_complete,
    resolve_after_material_booking_data_mutation,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


BOOKING_TABLE = BookingRow.__table__
CONTACT_TABLE = BookingContactRow.__table__
REVALIDATION_TABLE = BookingRevalidationRow.__table__

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_INPUT_RE = re.compile(r"^[0-9\s().+\-]+$")


class BookingContactValidationError(ValueError):
    """Raised when Booking contact data is structurally invalid."""


class BookingContactRevisionConflictError(RuntimeError):
    """Raised when the Booking changed after Contact was loaded."""


class BookingContactLockedError(RuntimeError):
    """Raised when Contact is edited in a terminal Booking."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.strip().split())
    return cleaned or None


def _clean_email(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    return cleaned.lower() if cleaned else None


def _clean_country_code(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    if _PHONE_INPUT_RE.fullmatch(cleaned) is None:
        raise BookingContactValidationError(
            "El código de país del teléfono contiene caracteres inválidos."
        )
    digits = "".join(char for char in cleaned if char.isdigit())
    if not 1 <= len(digits) <= 4:
        raise BookingContactValidationError(
            "El código de país debe contener entre 1 y 4 dígitos."
        )
    return f"+{digits}"


def _clean_phone(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    if _PHONE_INPUT_RE.fullmatch(cleaned) is None:
        raise BookingContactValidationError(
            "El teléfono contiene caracteres inválidos."
        )
    digits = "".join(char for char in cleaned if char.isdigit())
    if not 6 <= len(digits) <= 15:
        raise BookingContactValidationError(
            "El teléfono debe contener entre 6 y 15 dígitos."
        )
    return digits


class BookingContactService:
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

    def _row(self, booking_id: str):
        with self.booking_repository.engine.connect() as connection:
            return (
                connection.execute(
                    select(CONTACT_TABLE).where(
                        CONTACT_TABLE.c.booking_id == booking_id
                    )
                )
                .mappings()
                .first()
            )

    @staticmethod
    def _values_from_row(row) -> dict[str, object]:
        if row is None:
            return {
                "name": None,
                "email": None,
                "phone_country_code": None,
                "phone_number": None,
                "preferred_channel": None,
            }
        return {
            "name": row["name"],
            "email": row["email"],
            "phone_country_code": row["phone_country_code"],
            "phone_number": row["phone_number"],
            "preferred_channel": row["preferred_channel"],
        }

    @staticmethod
    def _normalize(
        request: BookingContactUpdateRequest,
    ) -> dict[str, object]:
        name = _clean_text(request.name)
        email = _clean_email(request.email)
        phone_country_code = _clean_country_code(
            request.phone_country_code
        )
        phone_number = _clean_phone(request.phone_number)
        preferred_channel = (
            request.preferred_channel.value
            if request.preferred_channel is not None
            else None
        )

        if email is not None and _EMAIL_RE.fullmatch(email) is None:
            raise BookingContactValidationError(
                "El email de contacto no es válido."
            )

        if (phone_country_code is None) != (phone_number is None):
            raise BookingContactValidationError(
                "Código de país y teléfono deben informarse juntos."
            )

        if (
            preferred_channel in {"phone", "whatsapp"}
            and phone_number is None
        ):
            raise BookingContactValidationError(
                "El canal elegido requiere un teléfono de contacto."
            )
        if preferred_channel == "email" and email is None:
            raise BookingContactValidationError(
                "El canal email requiere un email de contacto."
            )

        return {
            "name": name,
            "email": email,
            "phone_country_code": phone_country_code,
            "phone_number": phone_number,
            "preferred_channel": preferred_channel,
        }

    @staticmethod
    def _is_complete(values: dict[str, object]) -> bool:
        return contact_values_complete(
            name=values["name"],
            email=values["email"],
            phone_country_code=values["phone_country_code"],
            phone_number=values["phone_number"],
        )

    @staticmethod
    def _response(
        booking: BookingRecord,
        values: dict[str, object],
    ) -> BookingContactRecord:
        return BookingContactRecord(
            booking_id=booking.booking_id,
            booking_revision=booking.revision,
            name=values["name"],
            email=values["email"],
            phone_country_code=values["phone_country_code"],
            phone_number=values["phone_number"],
            preferred_channel=values["preferred_channel"],
            complete=BookingContactService._is_complete(values),
        )

    def get(self, booking_id: str) -> BookingContactRecord:
        booking = self._booking(booking_id)
        return self._response(
            booking,
            self._values_from_row(self._row(booking_id)),
        )

    def update(
        self,
        booking_id: str,
        request: BookingContactUpdateRequest,
    ) -> BookingContactRecord:
        booking = self._booking(booking_id)
        if booking.status in {
            BookingStatus.ABANDONED,
            BookingStatus.PNR_CREATED,
        }:
            raise BookingContactLockedError(
                f"No se puede editar contacto con Booking "
                f"{booking.status.value}."
            )

        current_row = self._row(booking_id)
        current_values = self._values_from_row(current_row)
        incoming = self._normalize(request)

        if incoming == current_values:
            # Safe retry of an already-persisted Contact payload.
            return self._response(booking, current_values)

        if request.revision != booking.revision:
            raise BookingContactRevisionConflictError(
                "El Booking cambió desde que abriste Contacto. "
                f"Recargá antes de guardar (actual {booking.revision}, "
                f"recibida {request.revision})."
            )

        contact_is_complete = self._is_complete(incoming)
        passengers_are_complete = passengers_complete(
            self.booking_repository,
            booking,
        )
        target_status, target_revalidation = (
            resolve_after_material_booking_data_mutation(
                booking,
                passengers_are_complete=passengers_are_complete,
                contact_is_complete=contact_is_complete,
            )
        )

        now = _utc_now()
        next_revision = booking.revision + 1

        try:
            with self.booking_repository.engine.begin() as connection:
                if current_row is None:
                    connection.execute(
                        insert(CONTACT_TABLE).values(
                            booking_id=booking_id,
                            **incoming,
                            updated_at=now,
                        )
                    )
                else:
                    connection.execute(
                        update(CONTACT_TABLE)
                        .where(CONTACT_TABLE.c.booking_id == booking_id)
                        .values(
                            **incoming,
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
                    raise BookingContactRevisionConflictError(
                        "El Booking fue modificado en paralelo. Recargá "
                        "antes de volver a guardar Contacto."
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
        except IntegrityError as exc:
            raise BookingContactRevisionConflictError(
                "Contacto fue modificado en paralelo. Recargá antes "
                "de volver a guardar."
            ) from exc

        updated_booking = self._booking(booking_id)
        return self._response(updated_booking, incoming)


def get_booking_contact_service() -> BookingContactService:
    return BookingContactService()
