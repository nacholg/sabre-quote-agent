from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.config import Settings
from app.sabre.soap_air_price import (
    _first_text,
    _matching_total_fare,
    build_air_price_body,
)
from app.sabre.soap_client import SabreSoapClient
from app.sabre.soap_pnr_read import (
    _parse_xml,
    _session_envelope,
    application_result_signals,
    application_results_status,
    build_session_close_body,
    build_travel_itinerary_read_body,
    count_flight_segments,
)
from app.sabre.soap_session import SabreSoapSessionService, SoapSession


class SabreSoapPqStoreError(RuntimeError):
    pass


class SabreSoapPqStoreReconciliationRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class SabreSoapPqStoreResult:
    currency: str
    total: Decimal
    host_command: str | None
    air_price_status: str
    end_transaction_status: str
    flight_segment_count: int
    session_close_ok: bool


def build_end_transaction_body(
    received_from: str = "SABRE QUOTE AGENT",
) -> str:
    value = received_from.strip() or "SABRE QUOTE AGENT"
    return f"""    <EndTransactionRQ
        xmlns="http://webservices.sabre.com/sabreXML/2011/10"
        Version="2.0.8">
      <EndTransaction Ind="true"/>
      <Source ReceivedFrom="{value}"/>
    </EndTransactionRQ>"""


def build_ignore_transaction_body() -> str:
    return """    <IgnoreTransactionRQ
        xmlns="http://webservices.sabre.com/sabreXML/2011/10"
        Version="2.0.0"/>"""


class SabreSoapPqStoreService:
    """Experimental CERT workflow to retain one priced PQ and EndTransaction.

    There is deliberately no automatic retry. If EndTransaction transport is
    ambiguous, the caller must reconcile the PNR before any new write.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: SabreSoapClient | None = None,
        session_service: SabreSoapSessionService | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or SabreSoapClient(
            settings.soap_endpoint,
            timeout=settings.sabre_timeout_seconds,
        )
        self.session_service = session_service or SabreSoapSessionService(
            settings,
            client=self.client,
        )

    def _call(
        self,
        session: SoapSession,
        *,
        action: str,
        service: str,
        body: str,
    ):
        xml = _session_envelope(
            self.settings,
            session,
            action=action,
            service=service,
            body=body,
        )
        return self.client.post(xml, soap_action=action)

    def _require_complete(
        self,
        root,
        *,
        operation: str,
    ) -> str:
        status = application_results_status(root)
        if status != "Complete":
            detail = "; ".join(
                application_result_signals(root)
            ) or "sin detalle"
            raise SabreSoapPqStoreError(
                f"{operation} no completó: "
                f"status={status or '-'}; {detail}"
            )
        return status

    def _price(
        self,
        session: SoapSession,
        *,
        currency: str,
        passenger_counts: dict[str, int],
        retain: bool,
    ) -> tuple[str, Decimal, str | None]:
        transport = self._call(
            session,
            action="OTA_AirPriceLLSRQ",
            service="OTA_AirPriceLLSRQ",
            body=build_air_price_body(
                currency,
                passenger_counts,
                retain=retain,
            ),
        )
        root = _parse_xml(
            transport,
            action="OTA_AirPriceLLSRQ",
        )
        status = self._require_complete(
            root,
            operation="OTA_AirPriceRQ",
        )
        total = _matching_total_fare(root, currency)
        if total is None:
            raise SabreSoapPqStoreError(
                "OTA_AirPriceRQ no devolvió TotalFare "
                f"en {currency.strip().upper()}."
            )
        return status, total, _first_text(root, "HostCommand")

    def _ignore(self, session: SoapSession) -> None:
        transport = self._call(
            session,
            action="IgnoreTransactionLLSRQ",
            service="IgnoreTransactionLLSRQ",
            body=build_ignore_transaction_body(),
        )
        root = _parse_xml(
            transport,
            action="IgnoreTransactionLLSRQ",
        )
        self._require_complete(
            root,
            operation="IgnoreTransactionRQ",
        )

    def _close_best_effort(self, session: SoapSession) -> bool:
        try:
            transport = self._call(
                session,
                action="SessionCloseRQ",
                service="SessionCloseRQ",
                body=build_session_close_body(self.settings),
            )
            _parse_xml(
                transport,
                action="SessionCloseRQ",
            )
            return True
        except Exception:
            return False

    def store(
        self,
        confirmation_id: str,
        *,
        currency: str,
        passenger_counts: dict[str, int],
        expected_total: Decimal,
        expected_segment_count: int,
        received_from: str = "SABRE QUOTE AGENT",
    ) -> SabreSoapPqStoreResult:
        if self.settings.sabre_env.strip().upper() != "CERT":
            raise SabreSoapPqStoreError(
                "Este workflow experimental sólo permite Sabre CERT."
            )

        if not self.settings.sabre_pnr_pricing_enabled:
            raise SabreSoapPqStoreError(
                "SABRE_PNR_PRICING_ENABLED debe ser true para este write."
            )

        expected_currency = currency.strip().upper()
        session = self.session_service.create()

        try:
            retrieve_transport = self._call(
                session,
                action="TravelItineraryReadRQ",
                service="TravelItineraryReadRQ",
                body=build_travel_itinerary_read_body(confirmation_id),
            )
            retrieve_root = _parse_xml(
                retrieve_transport,
                action="TravelItineraryReadRQ",
            )
            self._require_complete(
                retrieve_root,
                operation="TravelItineraryReadRQ",
            )
            segment_count = count_flight_segments(retrieve_root)
            if segment_count != expected_segment_count:
                raise SabreSoapPqStoreError(
                    "El PNR no coincide con el producto congelado: "
                    f"segments={segment_count}, "
                    f"expected={expected_segment_count}."
                )

            # First price is deliberately non-persisting. It acts as the final
            # pre-write price/currency gate in the same Sabre session.
            _, preview_total, _ = self._price(
                session,
                currency=expected_currency,
                passenger_counts=passenger_counts,
                retain=False,
            )
            if preview_total != expected_total:
                raise SabreSoapPqStoreError(
                    "PRICE_CHANGED antes de Retain: "
                    f"expected={expected_total}, got={preview_total}."
                )

            retained_status, retained_total, host_command = self._price(
                session,
                currency=expected_currency,
                passenger_counts=passenger_counts,
                retain=True,
            )
            if retained_total != expected_total:
                try:
                    self._ignore(session)
                except Exception as exc:
                    self._close_best_effort(session)
                    raise SabreSoapPqStoreReconciliationRequiredError(
                        "PRICE_CHANGED durante Retain y IgnoreTransaction "
                        "no pudo verificarse. No reintentar; verificar el "
                        "PNR/PQ en Sabre."
                    ) from exc

                raise SabreSoapPqStoreError(
                    "PRICE_CHANGED durante Retain; "
                    "la transacción fue descartada antes de EndTransaction."
                )

            try:
                end_transport = self._call(
                    session,
                    action="EndTransactionLLSRQ",
                    service="EndTransactionLLSRQ",
                    body=build_end_transaction_body(received_from),
                )
                end_root = _parse_xml(
                    end_transport,
                    action="EndTransactionLLSRQ",
                )
                end_status = self._require_complete(
                    end_root,
                    operation="EndTransactionRQ",
                )
            except Exception as exc:
                # EndTransaction was submitted. Never issue IgnoreTransaction
                # or blind-retry unless success was positively verified.
                self._close_best_effort(session)
                raise SabreSoapPqStoreReconciliationRequiredError(
                    "EndTransaction fue enviado pero no se pudo verificar "
                    "status=Complete. No reintentar; verificar el PNR/PQ "
                    "en Sabre."
                ) from exc

            close_ok = self._close_best_effort(session)
            return SabreSoapPqStoreResult(
                currency=expected_currency,
                total=retained_total,
                host_command=host_command,
                air_price_status=retained_status,
                end_transaction_status=end_status,
                flight_segment_count=segment_count,
                session_close_ok=close_ok,
            )

        except SabreSoapPqStoreReconciliationRequiredError:
            raise
        except Exception:
            self._close_best_effort(session)
            raise
