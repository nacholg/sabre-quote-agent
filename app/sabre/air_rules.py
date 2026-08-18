from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class AirRulesRequest:
    pcc: str
    conversation_id: str
    binary_security_token: str
    origin: str
    destination: str
    departure_date: date
    carrier: str
    fare_basis: str
    category: int = 16


@dataclass(frozen=True)
class AirRulesCategory:
    number: int | None
    title: str | None
    text: str


@dataclass(frozen=True)
class AirRulesParsedResponse:
    success: bool
    categories: tuple[AirRulesCategory, ...]
    fault_code: str | None = None
    fault_string: str | None = None
    application_errors: tuple[str, ...] = ()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def build_air_rules_request(request: AirRulesRequest) -> str:
    departure = request.departure_date.isoformat()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope
    xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:eb="http://www.ebxml.org/namespaces/messageHeader"
    xmlns:wsse="http://schemas.xmlsoap.org/ws/2002/12/secext">
  <soap-env:Header>
    <eb:MessageHeader eb:version="1.0" soap-env:mustUnderstand="1">
      <eb:From><eb:PartyId eb:type="urn:x12.org:IO5:01">Agency</eb:PartyId></eb:From>
      <eb:To><eb:PartyId eb:type="urn:x12.org:IO5:01">Sabre</eb:PartyId></eb:To>
      <eb:CPAId>{escape(request.pcc)}</eb:CPAId>
      <eb:ConversationId>{escape(request.conversation_id)}</eb:ConversationId>
      <eb:Service eb:type="sabreXML">OTA_AirRulesLLSRQ</eb:Service>
      <eb:Action>OTA_AirRulesLLSRQ</eb:Action>
    </eb:MessageHeader>
    <wsse:Security soap-env:mustUnderstand="1">
      <wsse:BinarySecurityToken>{escape(request.binary_security_token)}</wsse:BinarySecurityToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body>
    <OTA_AirRulesRQ xmlns="http://webservices.sabre.com/sabreXML/2011/10"
        Version="2.3.0" ReturnHostCommand="true">
      <OriginDestinationInformation>
        <DepartureDateTime>{departure}T00:00:00</DepartureDateTime>
        <OriginLocation LocationCode="{escape(request.origin)}"/>
        <DestinationLocation LocationCode="{escape(request.destination)}"/>
        <Airline Code="{escape(request.carrier)}"/>
      </OriginDestinationInformation>
      <FareBasis Code="{escape(request.fare_basis)}"/>
      <RuleReqInfo><Category>{request.category}</Category></RuleReqInfo>
    </OTA_AirRulesRQ>
  </soap-env:Body>
</soap-env:Envelope>
"""


def parse_air_rules_response(xml_text: str) -> AirRulesParsedResponse:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return AirRulesParsedResponse(False, (), fault_string="Invalid XML response")

    fault_code = None
    fault_string = None
    application_errors: list[str] = []
    categories: list[AirRulesCategory] = []

    for node in root.iter():
        name = _local(node.tag)
        text = (node.text or "").strip()
        if name == "faultcode" and text:
            fault_code = text
        elif name == "faultstring" and text:
            fault_string = text
        elif name == "Error" and text:
            application_errors.append(text)

    for node in root.iter():
        if _local(node.tag) not in {"Rule", "RuleInfo", "Category"}:
            continue
        number_raw = (
            node.attrib.get("Category")
            or node.attrib.get("Number")
            or node.attrib.get("CategoryNumber")
        )
        title = (
            node.attrib.get("Title")
            or node.attrib.get("Description")
            or node.attrib.get("Name")
        )
        texts = [
            (child.text or "").strip()
            for child in node.iter()
            if _local(child.tag) in {"Text", "Paragraph", "Line"}
            and (child.text or "").strip()
        ]
        if not texts:
            continue
        try:
            number = int(number_raw) if number_raw else None
        except ValueError:
            number = None
        categories.append(AirRulesCategory(number, title, "\n".join(texts)))

    return AirRulesParsedResponse(
        success=fault_code is None and fault_string is None and not application_errors,
        categories=tuple(categories),
        fault_code=fault_code,
        fault_string=fault_string,
        application_errors=tuple(application_errors),
    )
