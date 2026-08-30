from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.config import Settings
from app.sabre.client import SabreClient
from app.sabre.errors import (
    SabreAPIError,
    SabreWriteAmbiguousError,
    SabreWriteNotSentError,
)

_DIAGNOSTIC_SIGNAL_KEYS = {
    "bookingid",
    "category",
    "code",
    "confirmationid",
    "conversationid",
    "detail",
    "description",
    "details",
    "errorcode",
    "fieldname",
    "fieldpath",
    "fieldvalue",
    "message",
    "reason",
    "requestid",
    "severity",
    "status",
    "statuscode",
    "title",
    "transactionid",
    "type",
}

_DIAGNOSTIC_SKIP_BRANCHES = {
    "agency",
    "contactinfo",
    "formsofpayment",
    "identitydocuments",
    "passengers",
    "payment",
    "payments",
    "personname",
    "profiles",
    "travelers",
}


def _payload_sensitive_values(payload: dict[str, object]) -> set[str]:
    roots = (
        payload.get("travelers"),
        payload.get("contactInfo"),
        payload.get("identityDocuments"),
        payload.get("payment"),
        payload.get("payments"),
    )
    values: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, (str, int, float)):
            text = str(value).strip()
            if len(text) >= 3:
                values.add(text)

    for root in roots:
        walk(root)
    return values


def _redact_diagnostic_text(
    value: object,
    *,
    sensitive_values: set[str],
) -> str:
    text = str(value)

    for sensitive in sorted(
        sensitive_values,
        key=len,
        reverse=True,
    ):
        if not sensitive:
            continue
        text = re.sub(
            re.escape(sensitive),
            "[REDACTED]",
            text,
            flags=re.IGNORECASE,
        )

    text = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED_EMAIL]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)",
        "[REDACTED_NUMBER]",
        text,
    )
    return text[:240]


def sanitize_create_booking_response(
    body: object,
    payload: dict[str, object],
) -> dict[str, object]:
    sensitive_values = _payload_sensitive_values(payload)
    diagnostic: dict[str, object] = {
        "response_type": type(body).__name__,
    }

    if isinstance(body, dict):
        diagnostic["top_level_keys"] = sorted(
            str(key) for key in body.keys()
        )[:30]
    elif isinstance(body, str):
        diagnostic["text"] = _redact_diagnostic_text(
            body,
            sensitive_values=sensitive_values,
        )
        return diagnostic
    else:
        return diagnostic

    signals: list[dict[str, str]] = []

    def walk(value: object, path: str = "$") -> None:
        if len(signals) >= 20:
            return

        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                key_lower = key_text.lower()
                child_path = f"{path}.{key_text}"

                if key_lower in _DIAGNOSTIC_SKIP_BRANCHES:
                    continue

                if (
                    key_lower in _DIAGNOSTIC_SIGNAL_KEYS
                    and isinstance(item, (str, int, float, bool))
                ):
                    signals.append(
                        {
                            "path": child_path,
                            "value": _redact_diagnostic_text(
                                item,
                                sensitive_values=sensitive_values,
                            ),
                        }
                    )

                if isinstance(item, (dict, list)):
                    walk(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value[:20]):
                walk(item, f"{path}[{index}]")

    walk(body)
    diagnostic["signals"] = signals
    return diagnostic


def _diagnostic_from_response_text(
    text: str,
    payload: dict[str, object],
) -> dict[str, object] | None:
    if not text:
        return None
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        body = text
    return sanitize_create_booking_response(body, payload)


def _diagnostic_suffix(
    diagnostic: dict[str, object] | None,
) -> str:
    if not diagnostic:
        return ""
    rendered = json.dumps(
        diagnostic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f" diagnostic={rendered[:800]}"


def _attach_exchange_metadata(
    diagnostic: dict[str, object] | None,
    client: object,
) -> dict[str, object] | None:
    if diagnostic is None:
        return None
    exchange = getattr(client, "last_exchange", None)
    if not isinstance(exchange, dict):
        return diagnostic

    conversation_id = exchange.get("conversation_id")
    status_code = exchange.get("status_code")
    if isinstance(conversation_id, str) and conversation_id.strip():
        diagnostic["conversation_id"] = conversation_id.strip()
    if isinstance(status_code, int):
        diagnostic["http_status"] = status_code
    return diagnostic


class SabreCreateBookingDisabledError(RuntimeError):
    """Create Booking has not been explicitly enabled for this runtime."""


class SabreCreateBookingSafeFailure(RuntimeError):
    """Definitive failure where no Create Booking retry ambiguity exists."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostic: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(message + _diagnostic_suffix(diagnostic))


class SabreCreateBookingAmbiguousFailure(RuntimeError):
    """Sabre may have created the PNR; reconciliation is mandatory."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostic: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(message + _diagnostic_suffix(diagnostic))


@dataclass(frozen=True)
class SabreCreateBookingResult:
    confirmation_id: str
    provider_reference: str | None = None


def _explicit_bad_request_code(body: object) -> str | None:
    if not isinstance(body, dict):
        return None

    errors = body.get("errors")
    if not isinstance(errors, list):
        return None

    for error in errors:
        if not isinstance(error, dict):
            continue
        category = str(error.get("category") or "").strip().upper()
        if category != "BAD_REQUEST":
            continue
        error_type = str(error.get("type") or "").strip()
        return error_type or "PROVIDER_BAD_REQUEST"

    return None


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
                diagnostic = _attach_exchange_metadata(
                    _diagnostic_from_response_text(
                        exc.response_body,
                        payload,
                    ),
                    client,
                )
                if exc.status_code >= 500:
                    raise SabreCreateBookingAmbiguousFailure(
                        code,
                        str(exc),
                        diagnostic=diagnostic,
                    ) from exc
                raise SabreCreateBookingSafeFailure(
                    code,
                    str(exc),
                    diagnostic=diagnostic,
                ) from exc

            confirmation_id = _find_first_string(
                body,
                {"confirmationId"},
            )
            if not confirmation_id:
                bad_request_code = _explicit_bad_request_code(body)
                if bad_request_code:
                    diagnostic = _attach_exchange_metadata(
                        sanitize_create_booking_response(
                            body,
                            payload,
                        ),
                        client,
                    )
                    raise SabreCreateBookingSafeFailure(
                        bad_request_code,
                        "Sabre rechazó Create Booking con BAD_REQUEST explícito "
                        "y sin confirmationId. No se tratará como resultado "
                        "ambiguo.",
                        diagnostic=diagnostic,
                    )

                diagnostic = _attach_exchange_metadata(
                    sanitize_create_booking_response(
                        body,
                        payload,
                    ),
                    client,
                )
                raise SabreCreateBookingAmbiguousFailure(
                    "MISSING_CONFIRMATION_ID",
                    "Sabre respondió Create Booking sin un confirmationId "
                    "inequívoco. No se reintentará automáticamente.",
                    diagnostic=diagnostic,
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
