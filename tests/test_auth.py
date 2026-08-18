import base64

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.sabre.auth import SabreTokenProvider


def make_settings(**overrides):
    data = {
        "sabre_client_id": SecretStr("client"),
        "sabre_client_secret": SecretStr("secret"),
        "sabre_username": "743052-RY3A-AA",
        "sabre_password": SecretStr("password"),
        "sabre_pcc": "RY3A",
        "sabre_token_type": "password",
    }
    data.update(overrides)
    return Settings(**data)


@pytest.mark.asyncio
async def test_password_grant_uses_v3_and_standard_basic_auth():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "abc", "expires_in": 300})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        token = await SabreTokenProvider(make_settings(), client).get_token()

    assert token == "abc"
    assert captured["url"].endswith("/v3/auth/token")
    expected = base64.b64encode(b"client:secret").decode("ascii")
    assert captured["authorization"] == f"Basic {expected}"
    assert "grant_type=password" in captured["body"]
    assert "username=743052-RY3A-AA" in captured["body"]


@pytest.mark.asyncio
async def test_client_credentials_uses_v2():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "abc", "expires_in": 300})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SabreTokenProvider(
            make_settings(sabre_token_type="client_credentials"),
            client,
        )
        await provider.get_token()

    assert captured["url"].endswith("/v2/auth/token")
    assert captured["body"] == "grant_type=client_credentials"
