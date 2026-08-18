from datetime import date
from pathlib import Path

from app.sabre.air_rules import AirRulesRequest
from app.sabre.soap_client import SoapResult
from app.services.air_rules_service import AirRulesService


class FakeSoapClient:
    def __init__(self, result: SoapResult):
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        self.calls.append((xml, soap_action))
        return self.result


def request() -> AirRulesRequest:
    return AirRulesRequest(
        pcc="RY3A",
        conversation_id="test-conversation",
        binary_security_token="SECRET_TOKEN",
        origin="EZE",
        destination="MIA",
        departure_date=date(2027, 2, 10),
        carrier="AA",
        fare_basis="OLX0N1M1",
        category=16,
    )


def test_air_rules_service_success_and_raw_persistence(tmp_path: Path):
    response_xml = """<Envelope><Body><OTA_AirRulesRS>
      <Rule Category="16" Title="PENALTIES">
        <Text>CHANGES PERMITTED WITH FEE USD 200.</Text>
      </Rule>
    </OTA_AirRulesRS></Body></Envelope>"""

    client = FakeSoapClient(
        SoapResult(
            status_code=200,
            text=response_xml,
            content_type="text/xml",
            url="https://example.test/websvc",
        )
    )

    service = AirRulesService(
        client,
        output_dir=tmp_path,
    )

    result = service.lookup(
        request(),
        persist_raw=True,
    )

    assert result.ok is True
    assert result.parsed.success is True
    assert len(result.parsed.categories) == 1
    assert result.parsed.categories[0].number == 16

    assert len(client.calls) == 1
    sent_xml, action = client.calls[0]
    assert action == "OTA_AirRulesLLSRQ"
    assert "SECRET_TOKEN" in sent_xml

    assert result.request_path is not None
    assert result.response_path is not None
    assert result.request_path.exists()
    assert result.response_path.exists()

    saved_request = result.request_path.read_text(encoding="utf-8")
    assert "SECRET_TOKEN" not in saved_request
    assert "***REDACTED***" in saved_request


def test_air_rules_service_propagates_soap_fault():
    response_xml = """<Envelope><Body><Fault>
      <faultcode>soap-env:Client.InvalidSecurityToken</faultcode>
      <faultstring>Invalid security token</faultstring>
    </Fault></Body></Envelope>"""

    client = FakeSoapClient(
        SoapResult(
            status_code=500,
            text=response_xml,
            content_type="text/xml",
            url="https://example.test/websvc",
        )
    )

    result = AirRulesService(client).lookup(request())

    assert result.ok is False
    assert result.transport.status_code == 500
    assert result.parsed.success is False
    assert (
        result.parsed.fault_code
        == "soap-env:Client.InvalidSecurityToken"
    )


def test_air_rules_service_valid_response_without_category_16():
    response_xml = """<Envelope><Body><OTA_AirRulesRS>
      <Success/>
    </OTA_AirRulesRS></Body></Envelope>"""

    client = FakeSoapClient(
        SoapResult(
            status_code=200,
            text=response_xml,
            content_type="text/xml",
            url="https://example.test/websvc",
        )
    )

    result = AirRulesService(client).lookup(request())

    assert result.ok is True
    assert result.parsed.success is True
    assert result.parsed.categories == ()


def test_persist_raw_requires_output_dir():
    response_xml = "<Envelope><Body><OTA_AirRulesRS/></Body></Envelope>"

    client = FakeSoapClient(
        SoapResult(
            status_code=200,
            text=response_xml,
            content_type="text/xml",
            url="https://example.test/websvc",
        )
    )

    service = AirRulesService(client)

    try:
        service.lookup(request(), persist_raw=True)
    except ValueError as exc:
        assert "output_dir" in str(exc)
    else:
        raise AssertionError("Se esperaba ValueError")
