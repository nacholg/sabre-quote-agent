from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
from xml.etree import ElementTree as ET

from app.config import Settings
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
from app.sabre.soap_session import SabreSoapSessionService


def build_air_price_body(
    currency: str,
    passenger_counts: dict[str, int],
    *,
    retain: bool = False,
) -> str:
    currency_code = currency.strip().upper()
    if len(currency_code) != 3:
        raise ValueError("CurrencyCode inválido.")

    passenger_xml: list[str] = []
    for code in ("ADT", "CNN", "INF"):
        quantity = int(passenger_counts.get(code, 0))
        if quantity <= 0:
            continue
        passenger_xml.append(
            f'          <PassengerType Code="{escape(code)}" '
            f'Quantity="{quantity}"/>'
        )

    if not passenger_xml:
        raise ValueError("Se requiere al menos un pasajero para pricing.")

    passengers = "\n".join(passenger_xml)
    retain_attribute = ' Retain="true"' if retain else ""
    return f"""    <OTA_AirPriceRQ
        xmlns="http://webservices.sabre.com/sabreXML/2011/10"
        Version="2.14.0"
        ReturnHostCommand="true">
      <PriceRequestInformation{retain_attribute}>
        <OptionalQualifiers>
          <PricingQualifiers CurrencyCode="{escape(currency_code)}">
{passengers}
          </PricingQualifiers>
        </OptionalQualifiers>
      </PriceRequestInformation>
    </OTA_AirPriceRQ>"""


def _first_text(root: ET.Element, local_name: str) -> str | None:
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == local_name:
            text = (node.text or "").strip()
            if text:
                return text
    return None


def _matching_total_fare(
    root: ET.Element,
    currency: str,
) -> Decimal | None:
    wanted = currency.strip().upper()
    candidates: list[Decimal] = []

    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "TotalFare":
            continue
        node_currency = (
            node.attrib.get("CurrencyCode")
            or node.attrib.get("currencyCode")
            or ""
        ).strip().upper()
        if node_currency != wanted:
            continue

        raw = (
            node.attrib.get("Amount")
            or node.attrib.get("amount")
            or (node.text or "")
        ).strip()
        try:
            candidates.append(Decimal(raw))
        except (InvalidOperation, ValueError):
            continue

    if not candidates:
        return None
    return max(candidates)


@dataclass(frozen=True)
class SabreSoapAirPriceResult:
    currency: str
    total: Decimal
    host_command: str | None
    application_status: str
    flight_segment_count: int


class SabreSoapAirPriceService:
    """Quote an already-created PNR in a requested currency without retaining PQ."""

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

    def _close(self, session) -> None:
        close_xml = _session_envelope(
            self.settings,
            session,
            action="SessionCloseRQ",
            service="SessionCloseRQ",
            body=build_session_close_body(self.settings),
        )
        close_transport = self.client.post(
            close_xml,
            soap_action="SessionCloseRQ",
        )
        _parse_xml(
            close_transport,
            action="SessionCloseRQ",
        )

    def _close_best_effort(self, session) -> None:
        try:
            self._close(session)
        except Exception:
            # Cleanup must never replace the primary read/pricing error.
            pass

    def quote(
        self,
        confirmation_id: str,
        *,
        currency: str,
        passenger_counts: dict[str, int],
    ) -> SabreSoapAirPriceResult:
        session = self.session_service.create()
        try:
            retrieve_xml = _session_envelope(
                self.settings,
                session,
                action="TravelItineraryReadRQ",
                service="TravelItineraryReadRQ",
                body=build_travel_itinerary_read_body(confirmation_id),
            )
            retrieve_transport = self.client.post(
                retrieve_xml,
                soap_action="TravelItineraryReadRQ",
            )
            retrieve_root = _parse_xml(
                retrieve_transport,
                action="TravelItineraryReadRQ",
            )
            retrieve_status = application_results_status(retrieve_root)
            if retrieve_status != "Complete":
                detail = "; ".join(
                    application_result_signals(retrieve_root)
                ) or "sin detalle"
                raise RuntimeError(
                    "TravelItineraryReadRQ no completó: "
                    f"status={retrieve_status or '-'}; {detail}"
                )
            segment_count = count_flight_segments(retrieve_root)

            price_xml = _session_envelope(
                self.settings,
                session,
                action="OTA_AirPriceLLSRQ",
                service="OTA_AirPriceLLSRQ",
                body=build_air_price_body(currency, passenger_counts),
            )
            price_transport = self.client.post(
                price_xml,
                soap_action="OTA_AirPriceLLSRQ",
            )
            price_root = _parse_xml(
                price_transport,
                action="OTA_AirPriceLLSRQ",
            )
            price_status = application_results_status(price_root)
            if price_status != "Complete":
                detail = "; ".join(
                    application_result_signals(price_root)
                ) or "sin detalle"
                raise RuntimeError(
                    "OTA_AirPriceRQ no completó: "
                    f"status={price_status or '-'}; {detail}"
                )

            total = _matching_total_fare(price_root, currency)
            if total is None:
                raise RuntimeError(
                    "OTA_AirPriceRQ no devolvió TotalFare "
                    f"en {currency.strip().upper()}."
                )

            result = SabreSoapAirPriceResult(
                currency=currency.strip().upper(),
                total=total,
                host_command=_first_text(price_root, "HostCommand"),
                application_status=price_status,
                flight_segment_count=segment_count,
            )
        except Exception:
            self._close_best_effort(session)
            raise

        # Successful pricing still requires a clean session close.
        self._close(session)
        return result

