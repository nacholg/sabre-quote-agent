from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from uuid import uuid4
from xml.etree import ElementTree as ET

from app.config import Settings
from app.sabre.soap_client import SabreSoapClient, SoapResult


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _soap_username(settings: Settings) -> str:
    candidate = (
        (settings.sabre_username or "").strip()
        or (settings.sabre_epr or "").strip()
        or settings.resolved_username.strip()
    )
    if not candidate:
        raise RuntimeError("No pude resolver SABRE_USERNAME/SABRE_EPR.")
    return candidate.split("-", 1)[0].strip()


@dataclass(frozen=True)
class SoapSession:
    binary_security_token: str
    conversation_id: str
    transport: SoapResult


def build_session_create_request(
    settings: Settings,
    *,
    conversation_id: str,
) -> str:
    if settings.sabre_password is None:
        raise RuntimeError("Falta SABRE_PASSWORD.")

    username = _soap_username(settings)
    password = settings.sabre_password.get_secret_value()
    client_id = settings.sabre_client_id.get_secret_value()
    client_secret = settings.sabre_client_secret.get_secret_value()
    pcc = settings.sabre_pcc.strip().upper()

    # Sabre support confirmed SOAP domain AA for this EPR.
    domain = "AA"

    now = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
    )
    ttl = now + timedelta(minutes=5)

    now_text = now.isoformat().replace("+00:00", "Z")
    ttl_text = ttl.isoformat().replace("+00:00", "Z")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope
    xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
    xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
  <soap-env:Header>
    <eb:MessageHeader eb:version="1.0" soap-env:mustUnderstand="1">
      <eb:From>
        <eb:PartyId eb:type="urn:x12.org:IO5:01">Agency</eb:PartyId>
      </eb:From>
      <eb:To>
        <eb:PartyId eb:type="urn:x12.org:IO5:01">Sabre</eb:PartyId>
      </eb:To>
      <eb:CPAId>{escape(pcc)}</eb:CPAId>
      <eb:ConversationId>{escape(conversation_id)}</eb:ConversationId>
      <eb:Service eb:type="sabreXML">SessionCreateRQ</eb:Service>
      <eb:Action>SessionCreateRQ</eb:Action>
      <eb:MessageData>
        <eb:MessageId>mid:{uuid4()}</eb:MessageId>
        <eb:Timestamp>{now_text}</eb:Timestamp>
        <eb:TimeToLive>{ttl_text}</eb:TimeToLive>
      </eb:MessageData>
    </eb:MessageHeader>
    <wsse:Security soap-env:mustUnderstand="1">
      <wsse:UsernameToken>
        <wsse:Username>{escape(username)}</wsse:Username>
        <wsse:Password>{escape(password)}</wsse:Password>
        <Organization>{escape(pcc)}</Organization>
        <Domain>{domain}</Domain>
        <ClientId>{escape(client_id)}</ClientId>
        <ClientSecret>{escape(client_secret)}</ClientSecret>
      </wsse:UsernameToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body>
    <SessionCreateRQ
        xmlns="http://www.opentravel.org/OTA/2002/11"
        Version="1.0.0">
      <POS>
        <Source PseudoCityCode="{escape(pcc)}"/>
      </POS>
    </SessionCreateRQ>
  </soap-env:Body>
</soap-env:Envelope>
"""


def parse_session_token(xml_text: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    for node in root.iter():
        if _local(node.tag) == "BinarySecurityToken":
            value = (node.text or "").strip()
            if value:
                return value
    return None


def parse_session_diagnostic(xml_text: str) -> list[str]:
    """Extract safe SessionCreate response signals without tokens/raw XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ["response_xml=invalid"]

    safe_tags = {
        "Fault",
        "faultcode",
        "faultstring",
        "ApplicationResults",
        "Error",
        "Warning",
        "Message",
        "Description",
        "Status",
        "SystemSpecificResults",
    }
    blocked_tags = {
        "BinarySecurityToken",
        "Username",
        "Password",
        "ClientId",
        "ClientSecret",
    }
    safe_attrs = {"status", "type", "code", "ShortText", "message"}
    signals: list[str] = []

    for node in root.iter():
        local = _local(node.tag)
        if local in blocked_tags or local not in safe_tags:
            continue

        for key in safe_attrs:
            value = (node.attrib.get(key) or "").strip()
            if value:
                signals.append(f"{local}.{key}={value[:180]}")

        value = (node.text or "").strip()
        if value and len(value) <= 240:
            signals.append(f"{local}.text={value}")

        if len(signals) >= 16:
            break

    return signals or ["response_xml=no_safe_diagnostic_signals"]


def _redact_session_diagnostic(
    signals: list[str],
    sensitive_values: list[str],
) -> list[str]:
    result: list[str] = []
    for signal in signals:
        redacted = signal
        for sensitive in sensitive_values:
            value = (sensitive or "").strip()
            if len(value) >= 3:
                redacted = redacted.replace(value, "[REDACTED]")
        result.append(redacted)
    return result


class SabreSoapSessionService:
    def __init__(
        self,
        settings: Settings,
        *,
        client: SabreSoapClient | None = None,
    ):
        self.settings = settings
        self.client = client or SabreSoapClient(
            settings.soap_endpoint,
            timeout=settings.sabre_timeout_seconds,
        )

    def create(self) -> SoapSession:
        conversation_id = f"sabre-quote-agent-{uuid4()}"
        xml = build_session_create_request(
            self.settings,
            conversation_id=conversation_id,
        )

        transport = self.client.post(
            xml,
            soap_action="SessionCreateRQ",
        )
        token = parse_session_token(transport.text)

        if not transport.ok or not token:
            signals = parse_session_diagnostic(transport.text)
            sensitive_values = [
                (self.settings.sabre_username or "").strip(),
                (self.settings.sabre_epr or "").strip(),
                self.settings.resolved_username.strip(),
                (
                    self.settings.sabre_password.get_secret_value()
                    if self.settings.sabre_password is not None
                    else ""
                ),
                self.settings.sabre_client_id.get_secret_value(),
                self.settings.sabre_client_secret.get_secret_value(),
            ]
            signals = _redact_session_diagnostic(
                signals,
                sensitive_values,
            )
            diagnostic = "; ".join(signals)[:900]
            raise RuntimeError(
                "Sabre SOAP SessionCreateRQ falló: "
                f"HTTP={transport.status_code}; "
                f"content_type={transport.content_type or '-'}; "
                f"diagnostic={diagnostic}"
            )

        return SoapSession(
            binary_security_token=token,
            conversation_id=conversation_id,
            transport=transport,
        )
