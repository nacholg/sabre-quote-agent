from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.sabre.air_rules import (
    AirRulesParsedResponse,
    AirRulesRequest,
    build_air_rules_request,
    parse_air_rules_response,
)
from app.sabre.soap_client import SoapResult


class SoapPoster(Protocol):
    def post(self, xml: str, *, soap_action: str) -> SoapResult: ...


@dataclass(frozen=True)
class AirRulesLookupResult:
    request: AirRulesRequest
    transport: SoapResult
    parsed: AirRulesParsedResponse
    request_path: Path | None = None
    response_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.transport.ok and self.parsed.success


class AirRulesService:
    def __init__(
        self,
        client: SoapPoster,
        *,
        output_dir: Path | None = None,
    ):
        self.client = client
        self.output_dir = output_dir

    def lookup(
        self,
        request: AirRulesRequest,
        *,
        persist_raw: bool = False,
    ) -> AirRulesLookupResult:
        request_xml = build_air_rules_request(request)

        transport = self.client.post(
            request_xml,
            soap_action="OTA_AirRulesLLSRQ",
        )
        parsed = parse_air_rules_response(transport.text)

        request_path = None
        response_path = None

        if persist_raw:
            if self.output_dir is None:
                raise ValueError(
                    "persist_raw=True requiere output_dir en AirRulesService."
                )

            self.output_dir.mkdir(parents=True, exist_ok=True)
            suffix = uuid4().hex[:12]

            request_path = self.output_dir / f"air_rules_request_{suffix}.xml"
            response_path = self.output_dir / f"air_rules_response_{suffix}.xml"

            request_path.write_text(
                _redact_token(request_xml),
                encoding="utf-8",
            )
            response_path.write_text(
                transport.text,
                encoding="utf-8",
            )

        return AirRulesLookupResult(
            request=request,
            transport=transport,
            parsed=parsed,
            request_path=request_path,
            response_path=response_path,
        )


def _redact_token(xml_text: str) -> str:
    start_tag = "<wsse:BinarySecurityToken>"
    end_tag = "</wsse:BinarySecurityToken>"

    start = xml_text.find(start_tag)
    end = xml_text.find(end_tag)

    if start == -1 or end == -1 or end < start:
        return xml_text

    value_start = start + len(start_tag)
    return (
        xml_text[:value_start]
        + "***REDACTED***"
        + xml_text[end:]
    )
