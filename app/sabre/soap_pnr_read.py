from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from uuid import uuid4
from xml.etree import ElementTree as ET

from app.config import Settings
from app.models.pnr_workspace import PnrSnapshot
from app.sabre.pnr_snapshot_parser import parse_pnr_snapshot
from app.sabre.soap_client import SabreSoapClient, SoapResult
from app.sabre.soap_session import SabreSoapSessionService, SoapSession


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _utc_window() -> tuple[str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    ttl = now + timedelta(minutes=5)
    return (
        now.isoformat().replace("+00:00", "Z"),
        ttl.isoformat().replace("+00:00", "Z"),
    )


def _session_envelope(
    settings: Settings,
    session: SoapSession,
    *,
    action: str,
    service: str,
    body: str,
) -> str:
    pcc = escape(settings.sabre_pcc.strip().upper())
    conversation_id = escape(session.conversation_id)
    token = escape(session.binary_security_token)
    now_text, ttl_text = _utc_window()

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
      <eb:CPAId>{pcc}</eb:CPAId>
      <eb:ConversationId>{conversation_id}</eb:ConversationId>
      <eb:Service eb:type="sabreXML">{escape(service)}</eb:Service>
      <eb:Action>{escape(action)}</eb:Action>
      <eb:MessageData>
        <eb:MessageId>mid:{uuid4()}</eb:MessageId>
        <eb:Timestamp>{now_text}</eb:Timestamp>
        <eb:TimeToLive>{ttl_text}</eb:TimeToLive>
      </eb:MessageData>
    </eb:MessageHeader>
    <wsse:Security soap-env:mustUnderstand="1">
      <wsse:BinarySecurityToken>{token}</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body>
{body}
  </soap-env:Body>
</soap-env:Envelope>
"""


def build_travel_itinerary_read_body(confirmation_id: str) -> str:
    locator = confirmation_id.strip().upper()
    if len(locator) != 6:
        raise ValueError("Record locator inválido.")
    return f"""    <TravelItineraryReadRQ
        xmlns="http://services.sabre.com/res/tir/v3_10"
        Version="3.10.0">
      <MessagingDetails>
        <SubjectAreas>
          <SubjectArea>FULL</SubjectArea>
        </SubjectAreas>
      </MessagingDetails>
      <UniqueID ID="{escape(locator)}"/>
      <ReturnOptions UnmaskCreditCard="false"/>
    </TravelItineraryReadRQ>"""


def build_session_close_body(settings: Settings) -> str:
    pcc = escape(settings.sabre_pcc.strip().upper())
    return f"""    <SessionCloseRQ
        xmlns="http://www.opentravel.org/OTA/2002/11"
        Version="1.0.0">
      <POS>
        <Source PseudoCityCode="{pcc}"/>
      </POS>
    </SessionCloseRQ>"""


def _parse_xml(result: SoapResult, *, action: str) -> ET.Element:
    if not result.ok:
        raise RuntimeError(
            f"Sabre SOAP {action} HTTP {result.status_code}."
        )
    try:
        root = ET.fromstring(result.text)
    except ET.ParseError as exc:
        raise RuntimeError(
            f"Sabre SOAP {action} devolvió XML inválido."
        ) from exc

    for node in root.iter():
        if _local(node.tag) == "Fault":
            text = " ".join(
                (child.text or "").strip()
                for child in node.iter()
                if (child.text or "").strip()
            )
            raise RuntimeError(
                f"Sabre SOAP {action} Fault: {text[:800]}"
            )
    return root


def application_results_status(root: ET.Element) -> str | None:
    for node in root.iter():
        if _local(node.tag) == "ApplicationResults":
            value = (node.attrib.get("status") or "").strip()
            return value or None
    return None


def application_result_signals(root: ET.Element) -> list[str]:
    signals: list[str] = []
    allowed = {"Error", "Warning", "Message", "ShortText", "SystemSpecificResults"}
    for node in root.iter():
        local = _local(node.tag)
        if local not in allowed:
            continue
        for key in ("type", "code", "ShortText", "message"):
            value = (node.attrib.get(key) or "").strip()
            if value:
                signals.append(f"{local}.{key}={value[:180]}")
        text = (node.text or "").strip()
        if text and len(text) <= 240:
            signals.append(f"{local}.text={text}")
        if len(signals) >= 12:
            break
    return signals


def count_flight_segments(root: ET.Element) -> int:
    """Count booked air segments only, excluding duplicate PQ flight nodes."""

    for node in root.iter():
        if _local(node.tag) != "ReservationItems":
            continue
        count = 0
        for item in list(node):
            if _local(item.tag) != "Item":
                continue
            if any(
                _local(descendant.tag) == "FlightSegment"
                for descendant in item.iter()
                if descendant is not item
            ):
                count += 1
        return count
    return 0


@dataclass(frozen=True)
class SabreSoapPnrReadResult:
    confirmation_id: str
    application_status: str
    flight_segment_count: int
    snapshot: PnrSnapshot


class SabreSoapPnrReadService:
    """Read-only PNR retrieval over the existing Sabre SOAP stack."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: SabreSoapClient | None = None,
        session_service: SabreSoapSessionService | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or SabreSoapClient(
            settings.soap_endpoint,
            timeout=settings.sabre_timeout_seconds,
        )
        self.session_service = session_service or SabreSoapSessionService(
            settings,
            client=self.client,
        )

    def _close(self, session: SoapSession) -> None:
        close_xml = _session_envelope(
            self.settings,
            session,
            action="SessionCloseRQ",
            service="SessionCloseRQ",
            body=build_session_close_body(self.settings),
        )
        close_transport = self.client.post(
            close_xml,
            soap_action="SessionCloseRQ",
        )
        _parse_xml(
            close_transport,
            action="SessionCloseRQ",
        )

    def _close_best_effort(self, session: SoapSession) -> None:
        try:
            self._close(session)
        except Exception:
            # Read-only cleanup must never replace the primary Sabre error.
            pass

    def retrieve(
        self,
        confirmation_id: str,
    ) -> SabreSoapPnrReadResult:
        session = self.session_service.create()
        try:
            read_xml = _session_envelope(
                self.settings,
                session,
                action="TravelItineraryReadRQ",
                service="TravelItineraryReadRQ",
                body=build_travel_itinerary_read_body(confirmation_id),
            )
            transport = self.client.post(
                read_xml,
                soap_action="TravelItineraryReadRQ",
            )
            root = _parse_xml(
                transport,
                action="TravelItineraryReadRQ",
            )
            status = application_results_status(root)
            if status != "Complete":
                signals = application_result_signals(root)
                detail = "; ".join(signals) if signals else "sin detalle"
                raise RuntimeError(
                    "TravelItineraryReadRQ no completó correctamente: "
                    f"status={status or '-'}; {detail}"
                )

            snapshot = parse_pnr_snapshot(
                root,
                confirmation_id=confirmation_id,
                application_status=status,
            )
            result = SabreSoapPnrReadResult(
                confirmation_id=snapshot.confirmation_id,
                application_status=snapshot.application_status,
                flight_segment_count=len(snapshot.segments),
                snapshot=snapshot,
            )
        except Exception:
            self._close_best_effort(session)
            raise

        # On a successful read, a close failure is still operationally visible.
        self._close(session)
        return result
