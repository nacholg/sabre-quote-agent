from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.sabre.create_booking import (
    SabreCreateBookingAmbiguousFailure,
    SabreCreateBookingProvider,
    SabreCreateBookingSafeFailure,
)


class FakeBadRequestClient:
    last_exchange = {
        "conversation_id": "TEST-CONVERSATION",
        "status_code": 200,
    }

    async def post_once(self, path, payload, *, sensitive):
        return {
            "errors": [
                {
                    "category": "BAD_REQUEST",
                    "type": "UNABLE_TO_ADD_SPECIAL_SERVICE_CODE_NOT_ALLOWED",
                    "description": "Special service not allowed.",
                }
            ],
            "request": {},
            "timestamp": "2026-08-28T00:00:00Z",
        }


def test_http_200_bad_request_is_safe_failure_not_ambiguous() -> None:
    settings = Settings(
        _env_file=None,
        sabre_env="CERT",
        sabre_environment="cert",
        sabre_client_id="test-client-id",
        sabre_client_secret="test-client-secret",
        sabre_pcc="TEST",
        sabre_create_booking_enabled=True,
        sabre_create_booking_prod_enabled=False,
    )
    provider = SabreCreateBookingProvider(
        settings=settings,
        client=FakeBadRequestClient(),
    )

    async def run():
        await provider.create_booking(
            {
                "travelers": [],
                "contactInfo": {},
                "flightDetails": {},
            },
            environment="cert",
        )

    with pytest.raises(SabreCreateBookingSafeFailure) as captured:
        asyncio.run(run())

    assert (
        captured.value.code
        == "UNABLE_TO_ADD_SPECIAL_SERVICE_CODE_NOT_ALLOWED"
    )
    assert captured.value.diagnostic is not None
    assert captured.value.diagnostic["http_status"] == 200

class FakeContradictoryClient:
    last_exchange = {
        "conversation_id": "TEST-CONVERSATION",
        "status_code": 200,
    }

    async def post_once(self, path, payload, *, sensitive):
        return {
            "confirmationId": "ABC123",
            "errors": [
                {
                    "category": "BAD_REQUEST",
                    "type": "CONTRADICTORY_ERROR",
                    "description": "Contradictory response.",
                }
            ],
        }


class FakeMissingLocatorClient:
    last_exchange = {
        "conversation_id": "TEST-CONVERSATION",
        "status_code": 200,
    }

    async def post_once(self, path, payload, *, sensitive):
        return {
            "warnings": [{"message": "No locator returned."}],
        }


def _provider(client):
    settings = Settings(
        _env_file=None,
        sabre_env="CERT",
        sabre_environment="cert",
        sabre_client_id="test-client-id",
        sabre_client_secret="test-client-secret",
        sabre_pcc="TEST",
        sabre_create_booking_enabled=True,
        sabre_create_booking_prod_enabled=False,
    )
    return SabreCreateBookingProvider(
        settings=settings,
        client=client,
    )


def test_confirmation_id_wins_over_contradictory_bad_request() -> None:
    async def run():
        return await _provider(FakeContradictoryClient()).create_booking(
            {
                "travelers": [],
                "contactInfo": {},
                "flightDetails": {},
            },
            environment="cert",
        )

    result = asyncio.run(run())
    assert result.confirmation_id == "ABC123"


def test_missing_confirmation_without_explicit_rejection_stays_ambiguous() -> None:
    async def run():
        await _provider(FakeMissingLocatorClient()).create_booking(
            {
                "travelers": [],
                "contactInfo": {},
                "flightDetails": {},
            },
            environment="cert",
        )

    with pytest.raises(
        SabreCreateBookingAmbiguousFailure,
        match="confirmationId",
    ):
        asyncio.run(run())

