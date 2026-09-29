import httpx
import pytest

from app.config import Settings
from app.sabre.soap_client import SoapResult
from app.sabre.soap_secure_flight_docs import (
    SabreSoapSecureFlightDocsError,
    SabreSoapSecureFlightDocsProofService,
    SabreSoapSecureFlightDocsReconciliationRequiredError,
    build_secure_flight_docs_body,
)
from app.sabre.soap_session import SoapSession


def test_docs_body_uses_advance_passenger_document_and_existing_locator() -> None:
    xml = build_secure_flight_docs_body(
        "QRLVMD",
        given_name="CERTEXAMPLE",
        surname="BOOKING",
        date_of_birth="1985-04-15",
        gender="M",
        document_type="P",
        document_number="X1234567",
        issuing_country="AR",
        nationality="AR",
        expiry_date="2030-04-15",
    )

    assert 'version="3.5.0"' in xml
    assert '<UniqueID id="QRLVMD"/>' in xml
    assert '<AdvancePassenger SegmentNumber="A">' in xml
    assert 'ExpirationDate="2030-04-15"' in xml
    assert 'Number="X1234567"' in xml
    assert 'Type="P"' in xml
    assert "<IssueCountry>AR</IssueCountry>" in xml
    assert "<NationalityCountry>AR</NationalityCountry>" in xml
    assert 'DateOfBirth="1985-04-15"' in xml
    assert 'Gender="M"' in xml
    assert 'NameNumber="1.1"' in xml
    assert "<GivenName>CERTEXAMPLE</GivenName>" in xml
    assert "<Surname>BOOKING</Surname>" in xml
    assert '<EndTransaction Ind="true"/>' in xml
    assert 'SSR_Code="DOCS"' not in xml


def test_docs_body_rejects_non_passport_in_first_proof() -> None:
    with pytest.raises(SabreSoapSecureFlightDocsError, match="Type=P"):
        build_secure_flight_docs_body(
            "QRLVMD",
            given_name="CERTEXAMPLE",
            surname="BOOKING",
            date_of_birth="1985-04-15",
            gender="M",
            document_type="I",
            document_number="12345678",
            issuing_country="AR",
            nationality="AR",
            expiry_date="2030-04-15",
        )


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


class FakeClient:
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
<Error code="ERR.DOCS"><Message>DOCS NOT VERIFIED</Message></Error>
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


def _store(service: SabreSoapSecureFlightDocsProofService):
    return service.store(
        "QRLVMD",
        given_name="CERTEXAMPLE",
        surname="BOOKING",
        date_of_birth="1985-04-15",
        gender="M",
        document_type="P",
        document_number="X1234567",
        issuing_country="AR",
        nationality="AR",
        expiry_date="2030-04-15",
        expected_segment_count=2,
    )


def test_docs_write_requires_feature_gate_before_session() -> None:
    client = FakeClient()
    sessions = FakeSessionService()
    service = SabreSoapSecureFlightDocsProofService(
        _settings(enabled=False),
        client=client,
        session_service=sessions,
    )

    with pytest.raises(
        SabreSoapSecureFlightDocsError,
        match="SABRE_SECURE_FLIGHT_ENABLED",
    ):
        _store(service)

    assert sessions.create_count == 0
    assert client.actions == []


def test_docs_store_writes_exactly_once_and_uses_embedded_eot() -> None:
    client = FakeClient()
    service = SabreSoapSecureFlightDocsProofService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )

    result = _store(service)

    assert result.application_status == "Complete"
    assert result.flight_segment_count == 2
    assert result.session_close_ok is True
    assert client.actions == [
        "TravelItineraryReadRQ",
        "PassengerDetailsRQ",
        "SessionCloseRQ",
    ]
    assert client.actions.count("PassengerDetailsRQ") == 1
    assert client.xml[1].count("<EndTransactionRQ>") == 1
    assert "EndTransactionLLSRQ" not in client.xml[1]


@pytest.mark.parametrize("mode", ["transport", "xml", "not_processed"])
def test_ambiguous_or_unverified_submit_requires_reconciliation_no_retry(
    mode: str,
) -> None:
    client = FakeClient(passenger_mode=mode)
    service = SabreSoapSecureFlightDocsProofService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )

    with pytest.raises(
        SabreSoapSecureFlightDocsReconciliationRequiredError,
        match="NO RETRY",
    ):
        _store(service)

    assert client.actions.count("PassengerDetailsRQ") == 1
    assert client.actions[-1] == "SessionCloseRQ"


def test_cert_proof_script_never_accepts_document_number_as_cli_arg() -> None:
    from pathlib import Path

    source = Path("scripts/store_secure_flight_docs_cert.py").read_text(
        encoding="utf-8"
    )

    assert "--confirm-cert-write" in source
    assert "getpass(" in source
    assert "--document-number" not in source
    assert "assess_pnr_secure_flight_docs" in source
    assert "RESULT=NOT_REQUIRED" in source
    assert "RESULT=RECONCILIATION_REQUIRED" in source
    assert "RESULT=PROOF_SUCCEEDED" in source
