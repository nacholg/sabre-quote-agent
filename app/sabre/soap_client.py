from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class SoapResult:
    status_code: int
    text: str
    content_type: str | None
    url: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class SabreSoapClient:
    def __init__(self, endpoint: str, *, timeout: float = 60.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
            "Accept": "text/xml, application/xml",
        }
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.post(
                self.endpoint,
                headers=headers,
                content=xml.encode("utf-8"),
            )
        return SoapResult(
            status_code=response.status_code,
            text=response.text,
            content_type=response.headers.get("content-type"),
            url=str(response.url),
        )
