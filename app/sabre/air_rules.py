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
    """
    OTA_AirRulesLLSRQ 2.3.0 structure validated against Sabre CERT.

    Important schema details:
    - OriginDestinationInformation contains FlightSegment.
    - DepartureDateTime is a date (YYYY-MM-DD), not a timestamp.
    - FlightSegment child order is DestinationLocation,
      MarketingCarrier, OriginLocation.
    - RuleReqInfo is the final top-level request block.
    - Category precedes FareBasis inside RuleReqInfo.
    """
    departure = request.departure_date.isoformat()

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
    <OTA_AirRulesRQ
        xmlns="http://webservices.sabre.com/sabreXML/2011/10"
        Version="2.3.0"
        ReturnHostCommand="true">
      <OriginDestinationInformation>
        <FlightSegment DepartureDateTime="{departure}">
          <DestinationLocation LocationCode="{escape(request.destination)}"/>
          <MarketingCarrier Code="{escape(request.carrier)}"/>
          <OriginLocation LocationCode="{escape(request.origin)}"/>
        </FlightSegment>
      </OriginDestinationInformation>
      <RuleReqInfo>
        <Category>{request.category}</Category>
        <FareBasis Code="{escape(request.fare_basis)}"/>
      </RuleReqInfo>
    </OTA_AirRulesRQ>
  </soap-env:Body>
</soap-env:Envelope>
"""


def _node_text(node: ET.Element) -> str:
    """
    Preserve Sabre's paragraph text while also supporting responses where
    text is split into multiple Text/Line/Paragraph child nodes.
    """
    direct = (node.text or "").strip()
    child_texts = [
        (child.text or "").strip()
        for child in node.iter()
        if child is not node
        and _local(child.tag) in {"Text", "Line"}
        and (child.text or "").strip()
    ]

    if child_texts:
        return "\n".join(child_texts)

    return direct


def parse_air_rules_response(xml_text: str) -> AirRulesParsedResponse:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return AirRulesParsedResponse(
            False,
            (),
            fault_string="Invalid XML response",
        )

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
        elif name == "Error":
            messages = [
                (child.text or "").strip()
                for child in node.iter()
                if _local(child.tag) in {"Message", "ShortText"}
                and (child.text or "").strip()
            ]
            if messages:
                application_errors.extend(messages)
            elif text:
                application_errors.append(text)

    # Real OTA_AirRulesLLSRS 2.3.0 response:
    #
    # <Rules>
    #   <Paragraph RPH="16" Title="PENALTIES">
    #     <Text>...</Text>
    #   </Paragraph>
    # </Rules>
    #
    # Keep compatibility with the synthetic/offline shapes used during
    # development as well.
    for node in root.iter():
        name = _local(node.tag)

        if name == "Paragraph":
            number_raw = (
                node.attrib.get("RPH")
                or node.attrib.get("Category")
                or node.attrib.get("Number")
            )
            title = (
                node.attrib.get("Title")
                or node.attrib.get("Description")
                or node.attrib.get("Name")
            )
            text = _node_text(node)
        elif name in {"Rule", "RuleInfo", "Category"}:
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
            text = "\n".join(texts)
        else:
            continue

        if not text:
            continue

        try:
            number = int(number_raw) if number_raw else None
        except (TypeError, ValueError):
            number = None

        categories.append(
            AirRulesCategory(
                number=number,
                title=title,
                text=text,
            )
        )

    return AirRulesParsedResponse(
        success=(
            fault_code is None
            and fault_string is None
            and not application_errors
        ),
        categories=tuple(categories),
        fault_code=fault_code,
        fault_string=fault_string,
        application_errors=tuple(application_errors),
    )
