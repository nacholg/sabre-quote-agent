from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
import re
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
from app.sabre.soap_pq_store import (
    build_end_transaction_body,
    build_ignore_transaction_body,
)
from app.sabre.soap_session import SabreSoapSessionService, SoapSession


class SabreBrandPqStoreError(RuntimeError):
    pass


class SabreBrandPqStoreReconciliationRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class SabreBrandPriceResult:
    currency: str
    total: Decimal
    fare_basis: str | None
    validating_carrier: str | None
    last_day_to_purchase_raw: str | None
    host_command: str


@dataclass(frozen=True)
class SabreBrandPqStoreResult:
    preview: SabreBrandPriceResult
    retained: SabreBrandPriceResult
    end_transaction_status: str
    flight_segment_count: int
    session_close_ok: bool


def _normalize_code(value: str, *, label: str, pattern: str) -> str:
    normalized = str(value or "").strip().upper()
    if not re.fullmatch(pattern, normalized):
        raise ValueError(f"{label} inválido.")
    return normalized


def normalize_name_number(value: str) -> str:
    raw = str(value or "").strip()
    match = re.fullmatch(r"0*(\d+)\.0*(\d+)", raw)
    if not match:
        raise ValueError("NameNumber inválido.")
    return f"{int(match.group(1))}.{int(match.group(2))}"


def build_brand_price_command(
    *,
    currency: str,
    brand_code: str,
    segment_numbers: list[int],
    name_number: str,
    passenger_code: str,
    passenger_quantity: int = 1,
    retain: bool = False,
) -> str:
    currency_code = _normalize_code(
        currency,
        label="Currency",
        pattern=r"[A-Z]{3}",
    )
    brand = _normalize_code(
        brand_code,
        label="BrandCode",
        pattern=r"[A-Z0-9_-]{2,20}",
    )
    pax = _normalize_code(
        passenger_code,
        label="PassengerType",
        pattern=r"[A-Z0-9]{2,4}",
    )
    if passenger_quantity != 1:
        raise ValueError(
            "v0.35.11b CERT harness soporta exactamente 1 pasajero."
        )
    if not segment_numbers:
        raise ValueError("Se requiere al menos un segmento.")
    if any(int(number) < 1 for number in segment_numbers):
        raise ValueError("Segment number inválido.")

    name = normalize_name_number(name_number)
    pieces = [f"WPM{currency_code}"]
    pieces.extend(
        f"S{int(number)}*BR{brand}"
        for number in segment_numbers
    )
    pieces.append(f"N{name}")
    pieces.append(f"P1{pax}")
    if retain:
        pieces.append("RQ")
    return "¥".join(pieces)


def build_sabre_command_body(command: str) -> str:
    value = str(command or "").strip()
    if not value:
        raise ValueError("Sabre command vacío.")
    return (
        '    <SabreCommandLLSRQ '
        'xmlns="http://webservices.sabre.com/sabreXML/2011/10" '
        'Version="2.0.0" ReturnHostCommand="true">\n'
        '      <Request Output="SCREEN" CDATA="true">\n'
        f'        <HostCommand>{escape(value)}</HostCommand>\n'
        '      </Request>\n'
        '    </SabreCommandLLSRQ>'
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _screen_response(root: ET.Element) -> str:
    for node in root.iter():
        if _local(node.tag) == "Response":
            return (node.text or "").replace("\r", "")
    return ""


def _last_currency_amount(screen: str, currency: str) -> Decimal | None:
    """Return the priced passenger total, never ancillary fee amounts.

    v0.35.11b is deliberately limited to one passenger. Sabre's pricing
    screen associates the passenger total directly with the PTC, e.g.:

        USD808.13ADT

    Ancillary lines such as "2NDCHECKED BAG ... USD100.00" do not carry
    that PTC suffix and must never be interpreted as the ticket total.
    """

    code = re.escape(currency.upper())
    values: list[Decimal] = []
    pattern = (
        rf"\b{code}\s*(\d+(?:\.\d{{2}})?)"
        rf"\s*(?:ADT|CNN|CHD|INF)\b"
    )
    for raw in re.findall(pattern, screen.upper()):
        try:
            values.append(Decimal(raw))
        except InvalidOperation:
            continue
    return values[-1] if values else None


def parse_brand_price_screen(
    *,
    screen: str,
    currency: str,
    host_command: str,
) -> SabreBrandPriceResult:
    currency_code = currency.strip().upper()
    total = _last_currency_amount(screen, currency_code)
    if total is None:
        raise SabreBrandPqStoreError(
            f"SabreCommand no devolvió total en {currency_code}."
        )

    fare_basis = None
    fare_match = re.search(
        r"(?mi)^\s*(?:ADT|CNN|CHD|INF)[^\n]*?\s+([A-Z0-9]+(?:/[A-Z0-9]+)*)\s*$",
        screen,
    )
    if fare_match:
        fare_basis = fare_match.group(1).strip().upper()

    carrier = None
    carrier_match = re.search(
        r"(?mi)VALIDATING\s+CARRIER\s*-\s*([A-Z0-9]{2,3})",
        screen,
    )
    if carrier_match:
        carrier = carrier_match.group(1).strip().upper()

    deadline = None
    deadline_match = re.search(
        r"(?mi)LAST\s+DAY\s+TO\s+PURCHASE\s+([0-9]{1,2}[A-Z]{3}/[0-9]{4})",
        screen.upper(),
    )
    if deadline_match:
        deadline = deadline_match.group(1)

    return SabreBrandPriceResult(
        currency=currency_code,
        total=total,
        fare_basis=fare_basis,
        validating_carrier=carrier,
        last_day_to_purchase_raw=deadline,
        host_command=host_command,
    )


class SabreSoapBrandPqStoreService:
    """CERT-only price-by-brand PQ retention with fail-safe reconciliation.

    Workflow:
      retrieve -> price by brand (no retain) -> validate
      -> same price by brand + RQ -> validate -> EndTransaction.

    The retain command and EndTransaction are non-idempotent writes.
    There is no automatic retry.
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
        root: ET.Element,
        *,
        operation: str,
    ) -> str:
        status = application_results_status(root)
        if status != "Complete":
            detail = "; ".join(
                application_result_signals(root)
            ) or "sin detalle"
            raise SabreBrandPqStoreError(
                f"{operation} no completó: status={status or '-'}; {detail}"
            )
        return status

    def _retrieve_segment_count(
        self,
        session: SoapSession,
        confirmation_id: str,
    ) -> int:
        transport = self._call(
            session,
            action="TravelItineraryReadRQ",
            service="TravelItineraryReadRQ",
            body=build_travel_itinerary_read_body(confirmation_id),
        )
        root = _parse_xml(
            transport,
            action="TravelItineraryReadRQ",
        )
        self._require_complete(
            root,
            operation="TravelItineraryReadRQ",
        )
        return count_flight_segments(root)

    def _price_command(
        self,
        session: SoapSession,
        *,
        currency: str,
        brand_code: str,
        segment_numbers: list[int],
        name_number: str,
        passenger_code: str,
        retain: bool,
    ) -> SabreBrandPriceResult:
        command = build_brand_price_command(
            currency=currency,
            brand_code=brand_code,
            segment_numbers=segment_numbers,
            name_number=name_number,
            passenger_code=passenger_code,
            retain=retain,
        )
        transport = self._call(
            session,
            action="SabreCommandLLSRQ",
            service="SabreCommandLLSRQ",
            body=build_sabre_command_body(command),
        )
        root = _parse_xml(
            transport,
            action="SabreCommandLLSRQ",
        )
        self._require_complete(
            root,
            operation="SabreCommandLLSRQ",
        )
        screen = _screen_response(root)
        if not screen.strip():
            raise SabreBrandPqStoreError(
                "SabreCommandLLSRQ no devolvió pantalla de pricing."
            )
        return parse_brand_price_screen(
            screen=screen,
            currency=currency,
            host_command=command,
        )

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

    @staticmethod
    def _validate_price(
        result: SabreBrandPriceResult,
        *,
        expected_currency: str,
        expected_total: Decimal,
        expected_validating_carrier: str | None,
        phase: str,
    ) -> None:
        if result.currency != expected_currency:
            raise SabreBrandPqStoreError(
                f"{phase}: CURRENCY_CHANGED "
                f"expected={expected_currency}, got={result.currency}."
            )
        if result.total != expected_total:
            raise SabreBrandPqStoreError(
                f"{phase}: PRICE_CHANGED "
                f"expected={expected_total}, got={result.total}."
            )
        if expected_validating_carrier:
            carrier = (result.validating_carrier or "").strip().upper()
            if carrier != expected_validating_carrier:
                raise SabreBrandPqStoreError(
                    f"{phase}: VALIDATING_CARRIER_CHANGED "
                    f"expected={expected_validating_carrier}, "
                    f"got={carrier or '-'}."
                )

    def preview(
        self,
        confirmation_id: str,
        *,
        currency: str,
        brand_code: str,
        segment_numbers: list[int],
        name_number: str,
        passenger_code: str,
        expected_segment_count: int,
    ) -> SabreBrandPriceResult:
        if self.settings.sabre_env.strip().upper() != "CERT":
            raise SabreBrandPqStoreError(
                "Este workflow experimental sólo permite Sabre CERT."
            )

        session = self.session_service.create()
        try:
            segment_count = self._retrieve_segment_count(
                session,
                confirmation_id,
            )
            if segment_count != expected_segment_count:
                raise SabreBrandPqStoreError(
                    "El PNR no coincide con el producto esperado: "
                    f"segments={segment_count}, "
                    f"expected={expected_segment_count}."
                )
            result = self._price_command(
                session,
                currency=currency,
                brand_code=brand_code,
                segment_numbers=segment_numbers,
                name_number=name_number,
                passenger_code=passenger_code,
                retain=False,
            )
        except Exception:
            self._close_best_effort(session)
            raise

        self._close_best_effort(session)
        return result

    def store(
        self,
        confirmation_id: str,
        *,
        currency: str,
        brand_code: str,
        segment_numbers: list[int],
        name_number: str,
        passenger_code: str,
        expected_total: Decimal,
        expected_segment_count: int,
        expected_validating_carrier: str | None = None,
        received_from: str = "SABRE QUOTE AGENT",
    ) -> SabreBrandPqStoreResult:
        if self.settings.sabre_env.strip().upper() != "CERT":
            raise SabreBrandPqStoreError(
                "Este workflow experimental sólo permite Sabre CERT."
            )
        if not self.settings.sabre_pnr_pricing_enabled:
            raise SabreBrandPqStoreError(
                "SABRE_PNR_PRICING_ENABLED debe ser true para este write."
            )

        expected_currency = currency.strip().upper()
        expected_carrier = (
            str(expected_validating_carrier or "").strip().upper()
            or None
        )

        session = self.session_service.create()
        try:
            segment_count = self._retrieve_segment_count(
                session,
                confirmation_id,
            )
            if segment_count != expected_segment_count:
                raise SabreBrandPqStoreError(
                    "El PNR no coincide con el producto esperado: "
                    f"segments={segment_count}, "
                    f"expected={expected_segment_count}."
                )

            preview = self._price_command(
                session,
                currency=expected_currency,
                brand_code=brand_code,
                segment_numbers=segment_numbers,
                name_number=name_number,
                passenger_code=passenger_code,
                retain=False,
            )
            self._validate_price(
                preview,
                expected_currency=expected_currency,
                expected_total=expected_total,
                expected_validating_carrier=expected_carrier,
                phase="PREVIEW",
            )

            # From this point the next SabreCommand may mutate the PNR.
            try:
                retained = self._price_command(
                    session,
                    currency=expected_currency,
                    brand_code=brand_code,
                    segment_numbers=segment_numbers,
                    name_number=name_number,
                    passenger_code=passenger_code,
                    retain=True,
                )
            except Exception as exc:
                # If the retain command's outcome is ambiguous, first attempt
                # an explicit rollback in the same stateful session.
                try:
                    self._ignore(session)
                except Exception as ignore_exc:
                    self._close_best_effort(session)
                    raise SabreBrandPqStoreReconciliationRequiredError(
                        "PQ retain fue enviado pero no pudo verificarse, "
                        "y IgnoreTransaction tampoco pudo confirmarse. "
                        "No reintentar; releer *PQ/TIR antes de otro write."
                    ) from ignore_exc
                raise SabreBrandPqStoreError(
                    "PQ retain no pudo verificarse; "
                    "IgnoreTransaction confirmó rollback."
                ) from exc

            try:
                self._validate_price(
                    retained,
                    expected_currency=expected_currency,
                    expected_total=expected_total,
                    expected_validating_carrier=expected_carrier,
                    phase="RETAIN",
                )
            except Exception:
                try:
                    self._ignore(session)
                except Exception as ignore_exc:
                    self._close_best_effort(session)
                    raise SabreBrandPqStoreReconciliationRequiredError(
                        "PQ retenido no coincide con el esperado y "
                        "IgnoreTransaction no pudo confirmarse. "
                        "No reintentar; reconciliar el PNR."
                    ) from ignore_exc
                raise

            try:
                end_transport = self._call(
                    session,
                    action="EndTransactionLLSRQ",
                    service="EndTransactionLLSRQ",
                    body=build_end_transaction_body(received_from),
                )
            except Exception as exc:
                self._close_best_effort(session)
                raise SabreBrandPqStoreReconciliationRequiredError(
                    "EndTransaction transport ambiguity "
                    f"({type(exc).__name__}). "
                    "No reintentar; releer el PNR/PQ."
                ) from exc

            if not end_transport.ok:
                self._close_best_effort(session)
                raise SabreBrandPqStoreReconciliationRequiredError(
                    "EndTransaction HTTP no verificable: "
                    f"status={end_transport.status_code}. "
                    "No reintentar; releer el PNR/PQ."
                )

            try:
                end_root = _parse_xml(
                    end_transport,
                    action="EndTransactionLLSRQ",
                )
            except Exception as exc:
                self._close_best_effort(session)
                raise SabreBrandPqStoreReconciliationRequiredError(
                    "EndTransaction devolvió una respuesta no verificable "
                    f"({type(exc).__name__}). "
                    "No reintentar; releer el PNR/PQ."
                ) from exc

            end_status = application_results_status(end_root)
            if end_status != "Complete":
                signals = application_result_signals(end_root)
                detail = "; ".join(signals) if signals else "sin detalle"
                self._close_best_effort(session)
                raise SabreBrandPqStoreReconciliationRequiredError(
                    "EndTransaction respondió explícitamente "
                    f"status={end_status or '-'}; {detail}. "
                    "No reintentar automáticamente; reconciliar el PNR/PQ."
                )

            close_ok = self._close_best_effort(session)
            return SabreBrandPqStoreResult(
                preview=preview,
                retained=retained,
                end_transaction_status=end_status,
                flight_segment_count=segment_count,
                session_close_ok=close_ok,
            )

        except SabreBrandPqStoreReconciliationRequiredError:
            raise
        except Exception:
            self._close_best_effort(session)
            raise
