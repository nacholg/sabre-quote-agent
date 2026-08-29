from __future__ import annotations

from decimal import Decimal

from app.config import Settings
from app.sabre.soap_air_price import (
    SabreSoapAirPriceService,
    build_air_price_body,
)
from app.sabre.soap_client import SoapResult
from app.sabre.soap_session import SoapSession


class FakeSoapClient:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.xml: list[str] = []

    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        self.actions.append(soap_action)
        self.xml.append(xml)

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
        elif soap_action == "OTA_AirPriceLLSRQ":
            text = """<soap-env:Envelope
 xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:stl="http://services.sabre.com/STL/v01">
  <soap-env:Body>
    <OTA_AirPriceRS>
      <stl:ApplicationResults status="Complete">
        <stl:Success>
          <stl:SystemSpecificResults>
            <stl:HostCommand>WPMUSD¥P1ADT</stl:HostCommand>
          </stl:SystemSpecificResults>
        </stl:Success>
      </stl:ApplicationResults>
      <PricedItineraries>
        <PricedItinerary>
          <AirItineraryPricingInfo>
            <ItinTotalFare>
              <TotalFare Amount="991.43" CurrencyCode="USD"/>
            </ItinTotalFare>
          </AirItineraryPricingInfo>
        </PricedItinerary>
      </PricedItineraries>
    </OTA_AirPriceRS>
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


def test_air_price_body_forces_currency_without_retain() -> None:
    body = build_air_price_body("usd", {"ADT": 1})
    assert 'CurrencyCode="USD"' in body
    assert 'PassengerType Code="ADT" Quantity="1"' in body
    assert 'ReturnHostCommand="true"' in body
    assert "Retain=" not in body
    assert "EndTransaction" not in body


def test_air_price_quote_is_non_persisting() -> None:
    client = FakeSoapClient()
    service = SabreSoapAirPriceService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )

    result = service.quote(
        "OCIMSE",
        currency="USD",
        passenger_counts={"ADT": 1},
    )

    assert result.application_status == "Complete"
    assert result.currency == "USD"
    assert result.total == Decimal("991.43")
    assert result.host_command == "WPMUSD¥P1ADT"
    assert result.flight_segment_count == 2
    assert client.actions == [
        "TravelItineraryReadRQ",
        "OTA_AirPriceLLSRQ",
        "SessionCloseRQ",
    ]
    assert all("EndTransaction" not in xml for xml in client.xml)
    assert all('Retain="true"' not in xml for xml in client.xml)

class FakeAirPriceFailureClient(FakeSoapClient):
    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        if soap_action == "OTA_AirPriceLLSRQ":
            self.actions.append(soap_action)
            self.xml.append(xml)
            return SoapResult(
                status_code=200,
                text="""<Envelope>
  <Body>
    <OTA_AirPriceRS>
      <ApplicationResults status="NotProcessed">
        <Error type="BusinessLogic" code="ERR.PRICE">
          <Message>PRICE FAILED</Message>
        </Error>
      </ApplicationResults>
    </OTA_AirPriceRS>
  </Body>
</Envelope>""",
                content_type="text/xml",
                url="https://example.test/websvc",
            )
        if soap_action == "SessionCloseRQ":
            self.actions.append(soap_action)
            self.xml.append(xml)
            return SoapResult(
                status_code=500,
                text="<broken/>",
                content_type="text/xml",
                url="https://example.test/websvc",
            )
        return super().post(xml, soap_action=soap_action)


def test_air_price_failure_is_not_masked_by_session_close_failure() -> None:
    import pytest

    client = FakeAirPriceFailureClient()
    service = SabreSoapAirPriceService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )

    with pytest.raises(RuntimeError, match="status=NotProcessed"):
        service.quote(
            "OCIMSE",
            currency="USD",
            passenger_counts={"ADT": 1},
        )


class FakeCloseFailureAfterSuccessfulPriceClient(FakeSoapClient):
    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        if soap_action == "SessionCloseRQ":
            self.actions.append(soap_action)
            self.xml.append(xml)
            return SoapResult(
                status_code=500,
                text="<broken/>",
                content_type="text/xml",
                url="https://example.test/websvc",
            )
        return super().post(xml, soap_action=soap_action)


def test_successful_air_price_surfaces_session_close_failure() -> None:
    import pytest

    client = FakeCloseFailureAfterSuccessfulPriceClient()
    service = SabreSoapAirPriceService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )

    with pytest.raises(RuntimeError, match="SessionCloseRQ HTTP 500"):
        service.quote(
            "OCIMSE",
            currency="USD",
            passenger_counts={"ADT": 1},
        )

