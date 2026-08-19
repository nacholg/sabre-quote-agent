from types import SimpleNamespace

from pydantic import SecretStr

from app.sabre.soap_client import SoapResult
from app.sabre.soap_session import (
    SabreSoapSessionService,
    build_session_create_request,
)


def settings():
    return SimpleNamespace(
        sabre_username="743052-RY3A-AA",
        sabre_epr=None,
        resolved_username="743052-RY3A-AA",
        sabre_password=SecretStr("password"),
        sabre_client_id=SecretStr("client-id"),
        sabre_client_secret=SecretStr("client-secret"),
        sabre_pcc="RY3A",
        soap_endpoint="https://example.test/websvc",
        sabre_timeout_seconds=60.0,
    )


class FakeSoapClient:
    def post(self, xml: str, *, soap_action: str):
        assert soap_action == "SessionCreateRQ"
        assert "<wsse:Username>743052</wsse:Username>" in xml
        assert "<Organization>RY3A</Organization>" in xml
        assert "<Domain>AA</Domain>" in xml
        assert "<ClientId>client-id</ClientId>" in xml
        assert "<ClientSecret>client-secret</ClientSecret>" in xml
        return SoapResult(
            status_code=200,
            text=(
                "<Envelope><Header><Security>"
                "<BinarySecurityToken>TOKEN123</BinarySecurityToken>"
                "</Security></Header></Envelope>"
            ),
            content_type="text/xml",
            url="https://example.test/websvc",
        )


def test_session_request_uses_confirmed_sabre_credentials_shape():
    xml = build_session_create_request(
        settings(),
        conversation_id="conv-1",
    )

    assert "<wsse:Username>743052</wsse:Username>" in xml
    assert "<Organization>RY3A</Organization>" in xml
    assert "<Domain>AA</Domain>" in xml


def test_session_service_extracts_binary_security_token():
    session = SabreSoapSessionService(
        settings(),
        client=FakeSoapClient(),
    ).create()

    assert session.binary_security_token == "TOKEN123"
    assert session.conversation_id.startswith("sabre-quote-agent-")
