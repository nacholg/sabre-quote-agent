from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.config import Settings
from app.sabre.client import SabreClient
from app.sabre.errors import (
    SabreAPIError,
    SabreWriteAmbiguousError,
    SabreWriteNotSentError,
)


class SabreCreateBookingDisabledError(RuntimeError):
    """Create Booking has not been explicitly enabled for this runtime."""


class SabreCreateBookingSafeFailure(RuntimeError):
    """Definitive failure where no Create Booking retry ambiguity exists."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SabreCreateBookingAmbiguousFailure(RuntimeError):
    """Sabre may have created the PNR; reconciliation is mandatory."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SabreCreateBookingResult:
    confirmation_id: str
    provider_reference: str | None = None


def _find_first_string(
    value: object,
    keys: set[str],
) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _find_first_string(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_string(item, keys)
            if found:
                return found
    return None


class SabreCreateBookingProvider:
    provider_name = "sabre_booking_management"

    def __init__(
        self,
        *,
        settings: Settings,
        client: SabreClient | Any | None = None,
    ) -> None:
        self.settings = settings
        self.client = client

    def _assert_enabled(
        self,
        environment: Literal["cert", "prod"],
    ) -> None:
        actual = (
            "cert"
            if self.settings.sabre_env.strip().upper() == "CERT"
            else "prod"
        )
        if actual != environment:
            raise SabreCreateBookingDisabledError(
                f"Booking pertenece a {environment.upper()} pero el runtime "
                f"Sabre está en {actual.upper()}."
            )

        if not self.settings.sabre_create_booking_enabled:
            raise SabreCreateBookingDisabledError(
                "Create Booking está deshabilitado. "
                "Se requiere SABRE_CREATE_BOOKING_ENABLED=true."
            )

        if (
            environment == "prod"
            and not self.settings.sabre_create_booking_prod_enabled
        ):
            raise SabreCreateBookingDisabledError(
                "Create Booking PROD requiere además "
                "SABRE_CREATE_BOOKING_PROD_ENABLED=true."
            )

    async def create_booking(
        self,
        payload: dict[str, object],
        *,
        environment: Literal["cert", "prod"],
    ) -> SabreCreateBookingResult:
        self._assert_enabled(environment)

        own_client = self.client is None
        client = self.client or SabreClient(self.settings)

        try:
            try:
                body = await client.post_once(
                    self.settings.sabre_create_booking_path,
                    payload,
                    sensitive=True,
                )
            except SabreWriteNotSentError as exc:
                raise SabreCreateBookingSafeFailure(
                    "NOT_SENT",
                    str(exc),
                ) from exc
            except SabreWriteAmbiguousError as exc:
                raise SabreCreateBookingAmbiguousFailure(
                    "AMBIGUOUS_TRANSPORT",
                    str(exc),
                ) from exc
            except SabreAPIError as exc:
                code = f"HTTP_{exc.status_code}"
                if exc.status_code >= 500:
                    raise SabreCreateBookingAmbiguousFailure(
                        code,
                        str(exc),
                    ) from exc
                raise SabreCreateBookingSafeFailure(
                    code,
                    str(exc),
                ) from exc

            confirmation_id = _find_first_string(
                body,
                {"confirmationId"},
            )
            if not confirmation_id:
                raise SabreCreateBookingAmbiguousFailure(
                    "MISSING_CONFIRMATION_ID",
                    "Sabre respondió Create Booking sin un confirmationId "
                    "inequívoco. No se reintentará automáticamente.",
                )

            provider_reference = _find_first_string(
                body,
                {
                    "transactionId",
                    "transactionID",
                    "conversationId",
                    "conversationID",
                },
            )
            return SabreCreateBookingResult(
                confirmation_id=confirmation_id,
                provider_reference=provider_reference,
            )
        finally:
            if own_client:
                await client.close()
