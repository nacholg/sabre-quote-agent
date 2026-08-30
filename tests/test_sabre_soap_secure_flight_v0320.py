import httpx
import pytest

from app.config import Settings
from app.sabre.soap_client import SoapResult
from app.sabre.soap_secure_flight import (
    SabreSoapSecureFlightError,
    SabreSoapSecureFlightReconciliationRequiredError,
    SabreSoapSecureFlightService,
    build_secure_flight_body,
)
from app.sabre.soap_session import SoapSession


def test_secure_flight_body_uses_minimum_sf_pd_and_existing_locator() -> None:
    xml = build_secure_flight_body(
        "QRLVMD",
        given_name="CERTEXAMPLE",
        surname="BOOKING",
        date_of_birth="1985-04-15",
        gender="M",
    )

    assert 'version="3.5.0"' in xml
    assert 'haltOnError="true"' in xml
    assert 'ignoreOnError="true"' in xml
    assert '<UniqueID id="QRLVMD"/>' in xml
    assert '<SecureFlight SegmentNumber="A">' in xml
    assert 'DateOfBirth="1985-04-15"' in xml
    assert 'Gender="M"' in xml
    assert 'NameNumber="1.1"' in xml
    assert "<GivenName>CERTEXAMPLE</GivenName>" in xml
    assert "<Surname>BOOKING</Surname>" in xml
    assert '<EndTransaction Ind="true"/>' in xml
    assert "identityDocuments" not in xml
    assert "documentNumber" not in xml
    assert "AdvancePassenger" not in xml
    assert 'SSR_Code="DOCS"' not in xml


def test_secure_flight_body_escapes_names() -> None:
    xml = build_secure_flight_body(
        "ABC123",
        given_name="A&B",
        surname='O"NEIL',
        date_of_birth="1985-04-15",
        gender="F",
    )

    assert "A&amp;B" in xml
    assert 'O"NEIL' in xml

TIR = """<Envelope><Body><TravelItineraryReadRS>
<ApplicationResults status="Complete"/>
<TravelItinerary><ItineraryInfo><ReservationItems>
<Item><FlightSegment FlightNumber="900"/></Item>
<Item><FlightSegment FlightNumber="907"/></Item>
</ReservationItems></ItineraryInfo></TravelItinerary>
</TravelItineraryReadRS></Body></Envelope>"""

PASSENGER_COMPLETE = """<Envelope><Body><PassengerDetailsRS>
<ApplicationResults status="Complete"/>
</PassengerDetailsRS></Body></Envelope>"""

SESSION_CLOSE = """<Envelope><Body>
<SessionCloseRS status="Approved"/>
</Body></Envelope>"""


def _settings(*, enabled: bool = True, env: str = "CERT") -> Settings:
    return Settings(
        _env_file=None,
        sabre_env=env,
        sabre_environment=env.lower(),
        sabre_client_id="client",
        sabre_client_secret="secret",
        sabre_username="743052-RY3A-AA",
        sabre_password="password",
        sabre_pcc="RY3A",
        sabre_secure_flight_enabled=enabled,
    )


class FakeSessionService:
    def __init__(self) -> None:
        self.create_count = 0

    def create(self) -> SoapSession:
        self.create_count += 1
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


class FakeSecureFlightClient:
    def __init__(self, *, passenger_mode: str = "complete") -> None:
        self.passenger_mode = passenger_mode
        self.actions: list[str] = []
        self.xml: list[str] = []

    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        self.actions.append(soap_action)
        self.xml.append(xml)

        if soap_action == "TravelItineraryReadRQ":
            text = TIR
            status_code = 200
        elif soap_action == "PassengerDetailsRQ":
            if self.passenger_mode == "transport":
                raise httpx.ReadTimeout("ambiguous PassengerDetailsRQ")
            if self.passenger_mode == "http":
                return SoapResult(
                    status_code=500,
                    text="<error/>",
                    content_type="text/xml",
                    url="https://example.test/websvc",
                )
            if self.passenger_mode == "xml":
                return SoapResult(
                    status_code=200,
                    text="<broken",
                    content_type="text/xml",
                    url="https://example.test/websvc",
                )
            if self.passenger_mode == "not_processed":
                text = """<Envelope><Body><PassengerDetailsRS>
<ApplicationResults status="NotProcessed">
<Error code="ERR.SFPD"><Message>SFPD NOT VERIFIED</Message></Error>
</ApplicationResults>
</PassengerDetailsRS></Body></Envelope>"""
            else:
                text = PASSENGER_COMPLETE
            status_code = 200
        elif soap_action == "SessionCloseRQ":
            text = SESSION_CLOSE
            status_code = 200
        else:
            raise AssertionError(soap_action)

        return SoapResult(
            status_code=status_code,
            text=text,
            content_type="text/xml",
            url="https://example.test/websvc",
        )


def _service(
    client: FakeSecureFlightClient,
    *,
    settings: Settings | None = None,
    session_service: FakeSessionService | None = None,
) -> SabreSoapSecureFlightService:
    return SabreSoapSecureFlightService(
        settings or _settings(),
        client=client,
        session_service=session_service or FakeSessionService(),
    )


def _store(service: SabreSoapSecureFlightService):
    return service.store(
        "QRLVMD",
        given_name="CERTEXAMPLE",
        surname="BOOKING",
        date_of_birth="1985-04-15",
        gender="M",
        expected_segment_count=2,
    )


def test_service_requires_secure_flight_feature_gate_before_session() -> None:
    client = FakeSecureFlightClient()
    sessions = FakeSessionService()
    service = _service(
        client,
        settings=_settings(enabled=False),
        session_service=sessions,
    )

    with pytest.raises(
        SabreSoapSecureFlightError,
        match="SABRE_SECURE_FLIGHT_ENABLED",
    ):
        _store(service)

    assert sessions.create_count == 0
    assert client.actions == []


def test_invalid_secure_flight_payload_fails_before_session() -> None:
    client = FakeSecureFlightClient()
    sessions = FakeSessionService()
    service = _service(client, session_service=sessions)

    with pytest.raises(SabreSoapSecureFlightError, match="M, F o X"):
        service.store(
            "QRLVMD",
            given_name="CERTEXAMPLE",
            surname="BOOKING",
            date_of_birth="1985-04-15",
            gender="?",
            expected_segment_count=2,
        )

    assert sessions.create_count == 0
    assert client.actions == []


def test_secure_flight_store_writes_once_then_closes() -> None:
    client = FakeSecureFlightClient()
    result = _store(_service(client))

    assert result.application_status == "Complete"
    assert result.flight_segment_count == 2
    assert result.session_close_ok is True
    assert client.actions == [
        "TravelItineraryReadRQ",
        "PassengerDetailsRQ",
        "SessionCloseRQ",
    ]
    assert client.actions.count("PassengerDetailsRQ") == 1
    assert "<EndTransactionRQ>" in client.xml[1]
    assert '<EndTransaction Ind="true"/>' in client.xml[1]


@pytest.mark.parametrize(
    "mode",
    ["transport", "http", "xml", "not_processed"],
)
def test_unverified_passenger_details_requires_reconciliation_no_retry(
    mode: str,
) -> None:
    client = FakeSecureFlightClient(passenger_mode=mode)

    with pytest.raises(
        SabreSoapSecureFlightReconciliationRequiredError,
        match="NO RETRY",
    ):
        _store(_service(client))

    assert client.actions.count("PassengerDetailsRQ") == 1
    assert client.actions[-1] == "SessionCloseRQ"

