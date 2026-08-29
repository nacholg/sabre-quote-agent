from __future__ import annotations

from app.config import Settings
from app.sabre.soap_client import SoapResult
from app.sabre.soap_pnr_read import (
    SabreSoapPnrReadService,
    build_session_close_body,
    build_travel_itinerary_read_body,
)
from app.sabre.soap_session import SoapSession


class FakeSoapClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        self.calls.append((soap_action, xml))
        if soap_action == "TravelItineraryReadRQ":
            text = """<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <TravelItineraryReadRS>
      <ApplicationResults status="Complete"/>
      <TravelItinerary>
        <ItineraryInfo>
          <ReservationItems>
            <Item><FlightSegment FlightNumber="900"/></Item>
            <Item><FlightSegment FlightNumber="907"/></Item>
          </ReservationItems>
        </ItineraryInfo>
      </TravelItinerary>
    </TravelItineraryReadRS>
  </soap-env:Body>
</soap-env:Envelope>"""
        elif soap_action == "SessionCloseRQ":
            text = """<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body><SessionCloseRS status="Approved"/></soap-env:Body>
</soap-env:Envelope>"""
        else:
            raise AssertionError(soap_action)

        return SoapResult(
            status_code=200,
            text=text,
            content_type="text/xml",
            url="https://example.test/websvc",
        )


class FakeSessionService:
    def create(self) -> SoapSession:
        return SoapSession(
            binary_security_token="TEST-TOKEN",
            conversation_id="TEST-CONVERSATION",
            transport=SoapResult(
                status_code=200,
                text="<ok/>",
                content_type="text/xml",
                url="https://example.test/websvc",
            ),
        )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        sabre_env="CERT",
        sabre_environment="cert",
        sabre_client_id="client",
        sabre_client_secret="secret",
        sabre_username="743052-RY3A-AA",
        sabre_password="password",
        sabre_pcc="RY3A",
    )


def test_read_service_retrieves_and_closes_without_write() -> None:
    client = FakeSoapClient()
    service = SabreSoapPnrReadService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )

    result = service.retrieve("ocimse")

    assert result.confirmation_id == "OCIMSE"
    assert result.application_status == "Complete"
    assert result.flight_segment_count == 2
    assert [call[0] for call in client.calls] == [
        "TravelItineraryReadRQ",
        "SessionCloseRQ",
    ]
    assert "TEST-TOKEN" in client.calls[0][1]
    assert "OCIMSE" in client.calls[0][1]


def test_read_body_contains_locator_and_no_mutating_commands() -> None:
    body = build_travel_itinerary_read_body("ocimse")
    assert 'UniqueID ID="OCIMSE"' in body
    assert 'xmlns="http://services.sabre.com/res/tir/v3_10"' in body
    assert "<SubjectArea>FULL</SubjectArea>" in body
    assert 'Transaction Code="PNR"' not in body
    assert "OTA_AirPrice" not in body
    assert "EndTransaction" not in body
    assert "SabreCommand" not in body


def test_close_body_is_session_close_only() -> None:
    body = build_session_close_body(_settings())
    assert "SessionCloseRQ" in body
    assert "EndTransaction" not in body
    assert "OTA_AirPrice" not in body

class FakeFailedSoapClient(FakeSoapClient):
    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        if soap_action == "TravelItineraryReadRQ":
            text = """<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
  <soap-env:Body>
    <TravelItineraryReadRS>
      <ApplicationResults status="NotProcessed">
        <Error type="BusinessLogic" code="ERR.TEST">
          <Message>READ FAILED</Message>
        </Error>
      </ApplicationResults>
    </TravelItineraryReadRS>
  </soap-env:Body>
</soap-env:Envelope>"""
            return SoapResult(
                status_code=200,
                text=text,
                content_type="text/xml",
                url="https://example.test/websvc",
            )
        return super().post(xml, soap_action=soap_action)


def test_read_service_rejects_http_200_application_failure() -> None:
    import pytest

    client = FakeFailedSoapClient()
    service = SabreSoapPnrReadService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )

    with pytest.raises(RuntimeError, match="status=NotProcessed"):
        service.retrieve("OCIMSE")

class FakeReadAndCloseFailureClient(FakeFailedSoapClient):
    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        if soap_action == "SessionCloseRQ":
            return SoapResult(
                status_code=500,
                text="<broken/>",
                content_type="text/xml",
                url="https://example.test/websvc",
            )
        return super().post(xml, soap_action=soap_action)


def test_read_failure_is_not_masked_by_session_close_failure() -> None:
    import pytest

    client = FakeReadAndCloseFailureClient()
    service = SabreSoapPnrReadService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )

    with pytest.raises(RuntimeError, match="status=NotProcessed"):
        service.retrieve("OCIMSE")

