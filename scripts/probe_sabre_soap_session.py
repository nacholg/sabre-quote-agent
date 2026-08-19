from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from app.config import get_settings


def utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def xml_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_session_create(
    *,
    pcc: str,
    username: str,
    password: str,
    domain: str,
    conversation_id: str,
) -> str:
    now = utc_stamp()
    ttl = (
        datetime.now(timezone.utc) + timedelta(minutes=5)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    message_id = f"mid:{uuid.uuid4()}"

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
      <eb:CPAId>{xml_escape(pcc)}</eb:CPAId>
      <eb:ConversationId>{xml_escape(conversation_id)}</eb:ConversationId>
      <eb:Service eb:type="sabreXML">SessionCreateRQ</eb:Service>
      <eb:Action>SessionCreateRQ</eb:Action>
      <eb:MessageData>
        <eb:MessageId>{message_id}</eb:MessageId>
        <eb:Timestamp>{now}</eb:Timestamp>
        <eb:TimeToLive>{ttl}</eb:TimeToLive>
      </eb:MessageData>
    </eb:MessageHeader>
    <wsse:Security>
      <wsse:UsernameToken>
        <wsse:Username>{xml_escape(username)}</wsse:Username>
        <wsse:Password>{xml_escape(password)}</wsse:Password>
        <Organization>{xml_escape(pcc)}</Organization>
        <Domain>{xml_escape(domain)}</Domain>
      </wsse:UsernameToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body>
    <SessionCreateRQ
        xmlns="http://www.opentravel.org/OTA/2002/11"
        Version="1.0.0">
      <POS>
        <Source PseudoCityCode="{xml_escape(pcc)}"/>
      </POS>
    </SessionCreateRQ>
  </soap-env:Body>
</soap-env:Envelope>
"""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_details(xml_text: str) -> dict:
    details = {
        "binary_security_token": None,
        "fault_code": None,
        "fault_string": None,
        "error_texts": [],
    }

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return details

    for node in root.iter():
        name = local_name(node.tag)
        text = (node.text or "").strip()

        if name == "BinarySecurityToken" and text:
            details["binary_security_token"] = text
        elif name == "faultcode" and text:
            details["fault_code"] = text
        elif name == "faultstring" and text:
            details["fault_string"] = text
        elif name in {"Error", "SystemSpecificResults", "Message"} and text:
            details["error_texts"].append(text)

    return details


def redact_request(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return xml_text

    for node in root.iter():
        if local_name(node.tag) == "Password":
            node.text = "***REDACTED***"

    return ET.tostring(root, encoding="unicode")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe read-only de SessionCreateRQ para Sabre SOAP."
    )
    parser.add_argument("--env", choices=["cert", "prod"], default="cert")
    parser.add_argument("--endpoint", default=None, help="Override del SOAP endpoint.")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    settings = get_settings(args.env)

    if settings.sabre_password is None:
        raise RuntimeError("Falta SABRE_PASSWORD en el archivo de entorno.")

    username = settings.resolved_username
    if not username:
        raise RuntimeError("No pude resolver SABRE_USERNAME/SABRE_EPR.")

    password = settings.sabre_password.get_secret_value()
    endpoint = args.endpoint or settings.soap_endpoint

    conversation_id = f"sabre-quote-agent-{uuid.uuid4()}"
    request_xml = build_session_create(
        pcc=settings.sabre_pcc,
        username=username,
        password=password,
        domain=settings.sabre_aaa,
        conversation_id=conversation_id,
    )

    outdir = Path("output") / "air_rules_probe"
    outdir.mkdir(parents=True, exist_ok=True)

    (outdir / "session_create_request.xml").write_text(
        redact_request(request_xml),
        encoding="utf-8",
    )

    print("=== Sabre SOAP SessionCreateRQ probe ===")
    print(f"Environment: {args.env.upper()}")
    print(f"Endpoint: {endpoint}")
    print(f"PCC: {settings.sabre_pcc}")
    print(f"Username: {username}")
    print(f"Domain: {settings.sabre_aaa}")
    print()

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": '"SessionCreateRQ"',
        "Accept": "text/xml, application/xml",
    }

    try:
        with httpx.Client(timeout=args.timeout, follow_redirects=True) as client:
            response = client.post(
                endpoint,
                headers=headers,
                content=request_xml.encode("utf-8"),
            )
    except Exception as exc:
        report = {
            "ok": False,
            "stage": "transport",
            "environment": args.env,
            "endpoint": endpoint,
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        }
        (outdir / "session_create_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"TRANSPORT ERROR: {type(exc).__name__}: {exc}")
        print(f"Reporte: {outdir / 'session_create_report.json'}")
        return 2

    response_text = response.text
    (outdir / "session_create_response.xml").write_text(
        response_text,
        encoding="utf-8",
    )

    details = extract_details(response_text)
    token = details["binary_security_token"]

    report = {
        "ok": bool(response.is_success and token),
        "stage": "session_create",
        "environment": args.env,
        "endpoint": str(response.url),
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "has_binary_security_token": bool(token),
        "fault_code": details["fault_code"],
        "fault_string": details["fault_string"],
        "error_texts": details["error_texts"],
    }

    (outdir / "session_create_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"HTTP: {response.status_code}")
    print(f"Content-Type: {response.headers.get('content-type')}")

    if token:
        print("BinarySecurityToken: OK")
        print(f"Token length: {len(token)}")
        print("SESSION CREATE: SUCCESS")
        result = 0
    else:
        print("BinarySecurityToken: FALTA")
        if details["fault_code"]:
            print(f"Fault code: {details['fault_code']}")
        if details["fault_string"]:
            print(f"Fault string: {details['fault_string']}")
        print("SESSION CREATE: FAILED")
        result = 1

    print()
    print(f"Request redacted: {outdir / 'session_create_request.xml'}")
    print(f"Raw response:     {outdir / 'session_create_response.xml'}")
    print(f"Report:           {outdir / 'session_create_report.json'}")

    return result


if __name__ == "__main__":
    raise SystemExit(main())
