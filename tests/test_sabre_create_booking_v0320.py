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
