from __future__ import annotations

import pytest

from app.config import Settings
from app.sabre.soap_client import SoapResult
from app.sabre.soap_session import (
    SabreSoapSessionService,
    parse_session_diagnostic,
)


class FakeClient:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        assert soap_action == "SessionCreateRQ"
        return SoapResult(
            status_code=self.status_code,
            text=self.text,
            content_type="text/xml",
            url="https://example.test/websvc",
        )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        sabre_env="CERT",
        sabre_environment="cert",
        sabre_client_id="CLIENT-SECRET-VALUE",
        sabre_client_secret="CLIENT-VERY-SECRET",
        sabre_username="743052-RY3A-AA",
        sabre_password="PASSWORD-SECRET",
        sabre_pcc="RY3A",
    )


def test_session_diagnostic_extracts_fault_without_token() -> None:
    xml = """<Envelope><Fault><faultcode>soap:Client</faultcode><faultstring>Authentication failed</faultstring></Fault><BinarySecurityToken>DO-NOT-LEAK</BinarySecurityToken></Envelope>"""
    rendered = "; ".join(parse_session_diagnostic(xml))
    assert "soap:Client" in rendered
    assert "Authentication failed" in rendered
    assert "DO-NOT-LEAK" not in rendered


def test_session_service_surfaces_sanitized_failure() -> None:
    xml = """<Envelope><Fault><faultcode>soap:Client</faultcode><faultstring>Bad PASSWORD-SECRET credential</faultstring></Fault></Envelope>"""
    service = SabreSoapSessionService(
        _settings(),
        client=FakeClient(xml),
    )

    with pytest.raises(RuntimeError) as caught:
        service.create()

    message = str(caught.value)
    assert "HTTP=200" in message
    assert "soap:Client" in message
    assert "[REDACTED]" in message
    assert "PASSWORD-SECRET" not in message
    assert "CLIENT-VERY-SECRET" not in message
