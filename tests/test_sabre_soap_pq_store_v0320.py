from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.config import Settings
from app.sabre.soap_client import SoapResult
from app.sabre.soap_pq_store import (
    SabreSoapPqStoreReconciliationRequiredError,
    SabreSoapPqStoreService,
    build_end_transaction_body,
    build_ignore_transaction_body,
)
from app.sabre.soap_session import SoapSession


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
        sabre_pnr_pricing_enabled=True,
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


TIR = """<Envelope><Body><TravelItineraryReadRS>
<ApplicationResults status="Complete"/>
<TravelItinerary><ItineraryInfo><ReservationItems>
<Item><FlightSegment FlightNumber="900"/></Item>
<Item><FlightSegment FlightNumber="907"/></Item>
</ReservationItems></ItineraryInfo></TravelItinerary>
</TravelItineraryReadRS></Body></Envelope>"""

PRICE = """<Envelope><Body><OTA_AirPriceRS>
<ApplicationResults status="Complete">
<SystemSpecificResults><HostCommand>{host}</HostCommand></SystemSpecificResults>
</ApplicationResults>
<PricedItineraries><PricedItinerary><AirItineraryPricingInfo>
<ItinTotalFare><TotalFare Amount="{amount}" CurrencyCode="USD"/></ItinTotalFare>
</AirItineraryPricingInfo></PricedItinerary></PricedItineraries>
</OTA_AirPriceRS></Body></Envelope>"""

COMPLETE = """<Envelope><Body><RS><ApplicationResults status="Complete"/></RS></Body></Envelope>"""


class FakeClient:
    def __init__(
        self,
        *,
        retained_amount: str = "991.43",
        end_raises: bool = False,
    ) -> None:
        self.retained_amount = retained_amount
        self.end_raises = end_raises
        self.actions: list[str] = []
        self.xml: list[str] = []
        self.price_count = 0

    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        self.actions.append(soap_action)
        self.xml.append(xml)

        if soap_action == "TravelItineraryReadRQ":
            text = TIR
        elif soap_action == "OTA_AirPriceLLSRQ":
            self.price_count += 1
            retained = 'Retain="true"' in xml
            amount = self.retained_amount if retained else "991.43"
            host = "WPMUSD¥P1ADT¥RQ" if retained else "WPMUSD¥P1ADT"
            text = PRICE.format(host=host, amount=amount)
        elif soap_action == "EndTransactionLLSRQ":
            if self.end_raises:
                raise httpx.ReadTimeout("ambiguous end transaction")
            text = COMPLETE
        elif soap_action in {"IgnoreTransactionLLSRQ", "SessionCloseRQ"}:
            text = COMPLETE
        else:
            raise AssertionError(soap_action)

        return SoapResult(
            status_code=200,
            text=text,
            content_type="text/xml",
            url="https://example.test/websvc",
        )


def _service(client: FakeClient) -> SabreSoapPqStoreService:
    return SabreSoapPqStoreService(
        _settings(),
        client=client,
        session_service=FakeSessionService(),
    )


def test_store_pq_matches_then_end_transaction() -> None:
    client = FakeClient()
    result = _service(client).store(
        "OCIMSE",
        currency="USD",
        passenger_counts={"ADT": 1},
        expected_total=Decimal("991.43"),
        expected_segment_count=2,
    )

    assert result.currency == "USD"
    assert result.total == Decimal("991.43")
    assert result.end_transaction_status == "Complete"
    assert result.host_command == "WPMUSD¥P1ADT¥RQ"
    assert client.actions == [
        "TravelItineraryReadRQ",
        "OTA_AirPriceLLSRQ",
        "OTA_AirPriceLLSRQ",
        "EndTransactionLLSRQ",
        "SessionCloseRQ",
    ]
    assert 'Retain="true"' not in client.xml[1]
    assert 'Retain="true"' in client.xml[2]


def test_retained_price_change_is_ignored_before_end_transaction() -> None:
    client = FakeClient(retained_amount="999.99")

    with pytest.raises(RuntimeError, match="PRICE_CHANGED durante Retain"):
        _service(client).store(
            "OCIMSE",
            currency="USD",
            passenger_counts={"ADT": 1},
            expected_total=Decimal("991.43"),
            expected_segment_count=2,
        )

    assert "EndTransactionLLSRQ" not in client.actions
    assert "IgnoreTransactionLLSRQ" in client.actions
    assert client.actions[-1] == "SessionCloseRQ"


def test_ambiguous_end_transaction_requires_reconciliation_no_retry() -> None:
    client = FakeClient(end_raises=True)

    with pytest.raises(
        SabreSoapPqStoreReconciliationRequiredError,
        match="No reintentar",
    ):
        _service(client).store(
            "OCIMSE",
            currency="USD",
            passenger_counts={"ADT": 1},
            expected_total=Decimal("991.43"),
            expected_segment_count=2,
        )

    assert client.actions.count("EndTransactionLLSRQ") == 1
    assert client.actions[-1] == "SessionCloseRQ"


def test_transaction_bodies_are_explicit() -> None:
    end = build_end_transaction_body()
    ignore = build_ignore_transaction_body()

    assert 'EndTransaction Ind="true"' in end
    assert 'ReceivedFrom="SABRE QUOTE AGENT"' in end
    assert 'Version="2.0.8"' in end
    assert 'Version="2.0.0"' in ignore

def test_service_refuses_pq_write_when_feature_gate_disabled() -> None:
    from app.sabre.soap_pq_store import SabreSoapPqStoreError

    settings = Settings(
        _env_file=None,
        sabre_env="CERT",
        sabre_environment="cert",
        sabre_client_id="client",
        sabre_client_secret="secret",
        sabre_username="743052-RY3A-AA",
        sabre_password="password",
        sabre_pcc="RY3A",
        sabre_pnr_pricing_enabled=False,
    )
    client = FakeClient()
    service = SabreSoapPqStoreService(
        settings,
        client=client,
        session_service=FakeSessionService(),
    )

    with pytest.raises(
        SabreSoapPqStoreError,
        match="SABRE_PNR_PRICING_ENABLED",
    ):
        service.store(
            "OCIMSE",
            currency="USD",
            passenger_counts={"ADT": 1},
            expected_total=Decimal("991.43"),
            expected_segment_count=2,
        )

    assert client.actions == []


class EndHttpFailureClient(FakeClient):
    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        if soap_action == "EndTransactionLLSRQ":
            self.actions.append(soap_action)
            self.xml.append(xml)
            return SoapResult(
                status_code=500,
                text="<error/>",
                content_type="text/xml",
                url="https://example.test/websvc",
            )
        return super().post(xml, soap_action=soap_action)


class EndInvalidXmlClient(FakeClient):
    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        if soap_action == "EndTransactionLLSRQ":
            self.actions.append(soap_action)
            self.xml.append(xml)
            return SoapResult(
                status_code=200,
                text="<broken",
                content_type="text/xml",
                url="https://example.test/websvc",
            )
        return super().post(xml, soap_action=soap_action)


class EndNotProcessedClient(FakeClient):
    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        if soap_action == "EndTransactionLLSRQ":
            self.actions.append(soap_action)
            self.xml.append(xml)
            return SoapResult(
                status_code=200,
                text="""<Envelope><Body><EndTransactionRS>
<ApplicationResults status="NotProcessed">
<Error code="ERR.END"><Message>END NOT VERIFIED</Message></Error>
</ApplicationResults>
</EndTransactionRS></Body></Envelope>""",
                content_type="text/xml",
                url="https://example.test/websvc",
            )
        return super().post(xml, soap_action=soap_action)


@pytest.mark.parametrize(
    "client",
    [
        EndHttpFailureClient(),
        EndInvalidXmlClient(),
        EndNotProcessedClient(),
    ],
)
def test_unverified_end_transaction_requires_reconciliation_without_ignore(
    client: FakeClient,
) -> None:
    with pytest.raises(
        SabreSoapPqStoreReconciliationRequiredError,
        match="No reintentar",
    ):
        _service(client).store(
            "OCIMSE",
            currency="USD",
            passenger_counts={"ADT": 1},
            expected_total=Decimal("991.43"),
            expected_segment_count=2,
        )

    assert client.actions.count("EndTransactionLLSRQ") == 1
    assert "IgnoreTransactionLLSRQ" not in client.actions
    assert client.actions[-1] == "SessionCloseRQ"


class IgnoreFailureClient(FakeClient):
    def post(self, xml: str, *, soap_action: str) -> SoapResult:
        if soap_action == "IgnoreTransactionLLSRQ":
            self.actions.append(soap_action)
            self.xml.append(xml)
            raise httpx.ReadTimeout("ambiguous ignore transaction")
        return super().post(xml, soap_action=soap_action)


def test_failed_ignore_after_retained_price_change_requires_reconciliation() -> None:
    client = IgnoreFailureClient(retained_amount="999.99")

    with pytest.raises(
        SabreSoapPqStoreReconciliationRequiredError,
        match="IgnoreTransaction",
    ):
        _service(client).store(
            "OCIMSE",
            currency="USD",
            passenger_counts={"ADT": 1},
            expected_total=Decimal("991.43"),
            expected_segment_count=2,
        )

    assert "IgnoreTransactionLLSRQ" in client.actions
    assert "EndTransactionLLSRQ" not in client.actions
    assert client.actions[-1] == "SessionCloseRQ"

