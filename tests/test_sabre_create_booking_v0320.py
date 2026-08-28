from __future__ import annotations

import inspect
import json

import httpx
import pytest

from app.config import Settings
from app.sabre.client import SabreClient
from app.sabre.create_booking import (
    SabreCreateBookingAmbiguousFailure,
    SabreCreateBookingDisabledError,
    SabreCreateBookingProvider,
    SabreCreateBookingSafeFailure,
)
from app.sabre.errors import (
    SabreAPIError,
    SabreWriteAmbiguousError,
    SabreWriteNotSentError,
)


def _settings(**overrides) -> Settings:
    values = {
        "sabre_env": "CERT",
        "sabre_environment": "cert",
        "sabre_client_id": "client",
        "sabre_client_secret": "secret",
        "sabre_username": "user",
        "sabre_password": "password",
        "sabre_pcc": "RY3A",
        "sabre_create_booking_enabled": True,
        "sabre_create_booking_prod_enabled": False,
        "sabre_max_retries": 5,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeClient:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def post_once(self, path, payload, *, sensitive=False):
        self.calls.append((path, payload, sensitive))
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self):
        return None


class FakeTokens:
    async def get_token(self):
        return "TOKEN"


@pytest.mark.asyncio
async def test_provider_is_disabled_by_default() -> None:
    client = FakeClient(result={"confirmationId": "ABC123"})
    provider = SabreCreateBookingProvider(
        settings=_settings(sabre_create_booking_enabled=False),
        client=client,
    )

    with pytest.raises(SabreCreateBookingDisabledError):
        await provider.create_booking(
            {"travelers": []},
            environment="cert",
        )

    assert client.calls == []


@pytest.mark.asyncio
async def test_prod_requires_second_explicit_opt_in() -> None:
    client = FakeClient(result={"confirmationId": "ABC123"})
    provider = SabreCreateBookingProvider(
        settings=_settings(
            sabre_env="PROD",
            sabre_environment="production",
            sabre_create_booking_enabled=True,
            sabre_create_booking_prod_enabled=False,
            sabre_read_only=False,
        ),
        client=client,
    )

    with pytest.raises(SabreCreateBookingDisabledError):
        await provider.create_booking(
            {"travelers": []},
            environment="prod",
        )

    assert client.calls == []


@pytest.mark.asyncio
async def test_provider_extracts_nested_confirmation_id() -> None:
    client = FakeClient(
        result={
            "booking": {
                "confirmationId": "ABC123",
                "transactionId": "TX-1",
            }
        }
    )
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    result = await provider.create_booking(
        {"travelers": [{"givenName": "TEST"}]},
        environment="cert",
    )

    assert result.confirmation_id == "ABC123"
    assert result.provider_reference == "TX-1"
    assert len(client.calls) == 1
    assert client.calls[0][2] is True


@pytest.mark.asyncio
async def test_not_sent_maps_to_safe_failure() -> None:
    client = FakeClient(
        error=SabreWriteNotSentError("not sent")
    )
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    with pytest.raises(
        SabreCreateBookingSafeFailure,
        match="not sent",
    ) as caught:
        await provider.create_booking(
            {"travelers": []},
            environment="cert",
        )

    assert caught.value.code == "NOT_SENT"


@pytest.mark.asyncio
async def test_http_400_is_safe_failure() -> None:
    client = FakeClient(
        error=SabreAPIError(400, "Bad Request", "{}")
    )
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    with pytest.raises(SabreCreateBookingSafeFailure) as caught:
        await provider.create_booking(
            {"travelers": []},
            environment="cert",
        )

    assert caught.value.code == "HTTP_400"


@pytest.mark.asyncio
async def test_http_500_is_ambiguous() -> None:
    client = FakeClient(
        error=SabreAPIError(500, "Server Error", "{}")
    )
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    with pytest.raises(
        SabreCreateBookingAmbiguousFailure
    ) as caught:
        await provider.create_booking(
            {"travelers": []},
            environment="cert",
        )

    assert caught.value.code == "HTTP_500"


@pytest.mark.asyncio
async def test_success_without_confirmation_id_is_ambiguous() -> None:
    client = FakeClient(result={"status": "complete"})
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    with pytest.raises(
        SabreCreateBookingAmbiguousFailure
    ) as caught:
        await provider.create_booking(
            {"travelers": []},
            environment="cert",
        )

    assert caught.value.code == "MISSING_CONFIRMATION_ID"


@pytest.mark.asyncio
async def test_post_once_read_timeout_is_one_attempt_and_redacted() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("read timeout", request=request)

    client = SabreClient(_settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client.tokens = FakeTokens()

    payload = {
        "travelers": [
            {
                "givenName": "VERY_SECRET_TEST_NAME",
                "surname": "TEST",
            }
        ]
    }

    try:
        with pytest.raises(SabreWriteAmbiguousError):
            await client.post_once(
                "/v1/trip/orders/createBooking",
                payload,
                sensitive=True,
            )
    finally:
        await client.close()

    assert calls == 1
    assert client.last_exchange is not None
    rendered = json.dumps(client.last_exchange)
    assert "VERY_SECRET_TEST_NAME" not in rendered
    assert "request_json" not in client.last_exchange
    assert client.last_exchange["sensitive_payload_omitted"] is True


@pytest.mark.asyncio
async def test_post_once_connect_error_is_definitively_not_sent() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connect failed", request=request)

    client = SabreClient(_settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client.tokens = FakeTokens()

    try:
        with pytest.raises(SabreWriteNotSentError):
            await client.post_once(
                "/v1/trip/orders/createBooking",
                {"travelers": []},
                sensitive=True,
            )
    finally:
        await client.close()

    assert calls == 1


def test_post_once_source_has_no_retry_loop() -> None:
    source = inspect.getsource(SabreClient.post_once)
    assert "range(" not in source
    assert "asyncio.sleep" not in source

@pytest.mark.asyncio
async def test_missing_confirmation_persists_sanitized_diagnostic() -> None:
    payload = {
        "travelers": [
            {
                "givenName": "CERTTEST",
                "surname": "BOOKING",
                "birthDate": "1985-04-15",
            }
        ],
        "contactInfo": {
            "emails": ["test@example.com"],
            "phones": ["+541100000000"],
        },
        "flightDetails": {"flights": []},
    }
    client = FakeClient(
        result={
            "status": "INCOMPLETE",
            "bookingId": "TEMP-123",
            "errors": [
                {
                    "code": "ERR.TEST",
                    "severity": "ERROR",
                    "message": (
                        "Passenger CERTTEST BOOKING "
                        "test@example.com +541100000000 rejected"
                    ),
                }
            ],
            "travelers": [
                {"givenName": "CERTTEST", "surname": "BOOKING"}
            ],
        }
    )
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    with pytest.raises(
        SabreCreateBookingAmbiguousFailure
    ) as caught:
        await provider.create_booking(
            payload,
            environment="cert",
        )

    exc = caught.value
    assert exc.code == "MISSING_CONFIRMATION_ID"
    assert exc.diagnostic is not None

    rendered = json.dumps(
        exc.diagnostic,
        ensure_ascii=False,
    )
    message = str(exc)

    assert "ERR.TEST" in rendered
    assert "TEMP-123" in rendered
    assert "CERTTEST" not in rendered
    assert "BOOKING" not in rendered
    assert "test@example.com" not in rendered
    assert "+541100000000" not in rendered
    assert "CERTTEST" not in message
    assert "test@example.com" not in message


@pytest.mark.asyncio
async def test_booking_id_is_diagnostic_not_success_locator() -> None:
    client = FakeClient(
        result={
            "bookingId": "TEMP-ONLY",
            "status": "PENDING",
        }
    )
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    with pytest.raises(
        SabreCreateBookingAmbiguousFailure
    ) as caught:
        await provider.create_booking(
            {"travelers": []},
            environment="cert",
        )

    assert caught.value.code == "MISSING_CONFIRMATION_ID"
    assert caught.value.diagnostic is not None
    rendered = json.dumps(caught.value.diagnostic)
    assert "TEMP-ONLY" in rendered


@pytest.mark.asyncio
async def test_http_error_body_is_sanitized_into_diagnostic() -> None:
    response_body = json.dumps(
        {
            "errors": [
                {
                    "code": "INVALID_INPUT",
                    "message": "Bad traveler CERTTEST test@example.com",
                }
            ]
        }
    )
    client = FakeClient(
        error=SabreAPIError(
            400,
            "Bad Request",
            response_body,
        )
    )
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    with pytest.raises(
        SabreCreateBookingSafeFailure
    ) as caught:
        await provider.create_booking(
            {
                "travelers": [{"givenName": "CERTTEST"}],
                "contactInfo": {
                    "emails": ["test@example.com"],
                },
            },
            environment="cert",
        )

    assert caught.value.code == "HTTP_400"
    rendered = json.dumps(caught.value.diagnostic)
    assert "INVALID_INPUT" in rendered
    assert "CERTTEST" not in rendered
    assert "test@example.com" not in rendered

@pytest.mark.asyncio
async def test_post_once_sends_conversation_id_header() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["conversation_id"] = request.headers.get("Conversation-ID")
        return httpx.Response(
            200,
            json={"confirmationId": "ABC123"},
            request=request,
        )

    client = SabreClient(_settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client.tokens = FakeTokens()

    try:
        result = await client.post_once(
            "/v1/trip/orders/createBooking",
            {"travelers": []},
            sensitive=True,
        )
    finally:
        await client.close()

    assert result["confirmationId"] == "ABC123"
    assert seen["conversation_id"]
    assert client.last_exchange is not None
    assert (
        client.last_exchange["request_headers"]["Conversation-ID"]
        == seen["conversation_id"]
    )
    assert client.last_exchange["conversation_id"] == seen["conversation_id"]


@pytest.mark.asyncio
async def test_sanitized_diagnostic_keeps_error_description_and_field_path() -> None:
    payload = {
        "travelers": [{"givenName": "CERTTEST"}],
        "contactInfo": {"emails": ["test@example.com"]},
    }
    client = FakeClient(
        result={
            "errors": [
                {
                    "category": "APPLICATION_ERROR",
                    "type": "UNABLE_TO_BOOK_FLIGHTS",
                    "description": (
                        "Traveler CERTTEST could not be booked; "
                        "contact test@example.com"
                    ),
                    "fieldName": "bookingClass",
                    "fieldPath": "flightDetails.flights[0].bookingClass",
                    "fieldValue": "O",
                    "reason": "HOST_REJECTED",
                }
            ]
        }
    )
    provider = SabreCreateBookingProvider(
        settings=_settings(),
        client=client,
    )

    with pytest.raises(
        SabreCreateBookingAmbiguousFailure
    ) as caught:
        await provider.create_booking(
            payload,
            environment="cert",
        )

    rendered = json.dumps(
        caught.value.diagnostic,
        ensure_ascii=False,
    )
    assert "UNABLE_TO_BOOK_FLIGHTS" in rendered
    assert "flightDetails.flights[0].bookingClass" in rendered
    assert "HOST_REJECTED" in rendered
    assert "CERTTEST" not in rendered
    assert "test@example.com" not in rendered
