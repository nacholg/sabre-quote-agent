from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape

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
from app.sabre.soap_session import SabreSoapSessionService, SoapSession


class SabreSoapSecureFlightDocsError(RuntimeError):
    pass


class SabreSoapSecureFlightDocsReconciliationRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class SabreSoapSecureFlightDocsResult:
    application_status: str
    flight_segment_count: int
    session_close_ok: bool


def _xml_attr(value: str) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _xml_text(value: str) -> str:
    return escape(str(value))


def _iso_date(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise SabreSoapSecureFlightDocsError(
            f"{label} debe usar formato YYYY-MM-DD."
        ) from exc


def _country(value: str, *, label: str) -> str:
    normalized = str(value or "").strip().upper()
    if len(normalized) != 2 or not normalized.isalpha():
        raise SabreSoapSecureFlightDocsError(
            f"{label} debe ser ISO alpha-2."
        )
    return normalized


def build_secure_flight_docs_body(
    confirmation_id: str,
    *,
    given_name: str,
    surname: str,
    date_of_birth: str,
    gender: str,
    document_type: str,
    document_number: str,
    issuing_country: str,
    nationality: str,
    expiry_date: str,
    name_number: str = "1.1",
    received_from: str = "SABRE QUOTE AGENT",
) -> str:
    locator = _xml_attr(str(confirmation_id or "").strip().upper())
    given = _xml_text(str(given_name or "").strip().upper())
    family = _xml_text(str(surname or "").strip().upper())
    dob = _xml_attr(_iso_date(date_of_birth, label="Date of birth"))
    sex = _xml_attr(str(gender or "").strip().upper())
    number = _xml_attr(str(name_number or "").strip())
    received = _xml_attr(
        str(received_from or "").strip() or "SABRE QUOTE AGENT"
    )

    doc_type = _xml_attr(str(document_type or "").strip().upper())
    doc_number = _xml_attr(str(document_number or "").strip().upper())
    issue_country = _xml_text(
        _country(issuing_country, label="Issuing country")
    )
    nationality_country = _xml_text(
        _country(nationality, label="Nationality")
    )
    expiration = _xml_attr(_iso_date(expiry_date, label="Expiry date"))

    if not locator or not given or not family or not sex or not number:
        raise SabreSoapSecureFlightDocsError(
            "DOCS requiere locator, nombre, apellido, DOB, género y NameNumber."
        )
    if sex not in {"M", "F", "X"}:
        raise SabreSoapSecureFlightDocsError(
            "v0.36.1 sólo admite M, F o X en este proof CERT."
        )
    if doc_type != "P":
        raise SabreSoapSecureFlightDocsError(
            "v0.36.1 limita el proof CERT a documento Type=P (passport)."
        )
    if not doc_number:
        raise SabreSoapSecureFlightDocsError(
            "Document number es obligatorio para el proof DOCS."
        )

    return f"""    <PassengerDetailsRQ
        xmlns="http://services.sabre.com/sp/pd/v3_5"
        version="3.5.0"
        ignoreOnError="true"
        haltOnError="true">
      <PostProcessing ignoreAfter="false" unmaskCreditCard="false">
        <RedisplayReservation/>
        <EndTransactionRQ>
          <EndTransaction Ind="true"/>
          <Source ReceivedFrom="{received}"/>
        </EndTransactionRQ>
      </PostProcessing>
      <PreProcessing ignoreBefore="false">
        <UniqueID id="{locator}"/>
      </PreProcessing>
      <SpecialReqDetails>
        <SpecialServiceRQ>
          <SpecialServiceInfo>
            <AdvancePassenger SegmentNumber="A">
              <Document
                  ExpirationDate="{expiration}"
                  Number="{doc_number}"
                  Type="{doc_type}">
                <IssueCountry>{issue_country}</IssueCountry>
                <NationalityCountry>{nationality_country}</NationalityCountry>
              </Document>
              <PersonName
                  DateOfBirth="{dob}"
                  Gender="{sex}"
                  NameNumber="{number}">
                <GivenName>{given}</GivenName>
                <Surname>{family}</Surname>
              </PersonName>
            </AdvancePassenger>
          </SpecialServiceInfo>
        </SpecialServiceRQ>
      </SpecialReqDetails>
    </PassengerDetailsRQ>"""


class SabreSoapSecureFlightDocsProofService:
    """CERT-only v0.36.1 proof for one APIS/DOCS write.

    PassengerDetailsRQ contains the EndTransaction. There is deliberately no
    automatic retry. Any ambiguous outcome after submission requires explicit
    reconciliation before another write.
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

    def _close_best_effort(self, session: SoapSession) -> bool:
        try:
            transport = self._call(
                session,
                action="SessionCloseRQ",
                service="SessionCloseRQ",
                body=build_session_close_body(self.settings),
            )
            _parse_xml(transport, action="SessionCloseRQ")
            return True
        except Exception:
            return False

    def store(
        self,
        confirmation_id: str,
        *,
        given_name: str,
        surname: str,
        date_of_birth: str,
        gender: str,
        document_type: str,
        document_number: str,
        issuing_country: str,
        nationality: str,
        expiry_date: str,
        expected_segment_count: int,
        name_number: str = "1.1",
        received_from: str = "SABRE QUOTE AGENT",
    ) -> SabreSoapSecureFlightDocsResult:
        if self.settings.sabre_env.strip().upper() != "CERT":
            raise SabreSoapSecureFlightDocsError(
                "Este proof sólo permite Sabre CERT."
            )
        if not self.settings.sabre_secure_flight_enabled:
            raise SabreSoapSecureFlightDocsError(
                "SABRE_SECURE_FLIGHT_ENABLED debe ser true para este write."
            )

        body = build_secure_flight_docs_body(
            confirmation_id,
            given_name=given_name,
            surname=surname,
            date_of_birth=date_of_birth,
            gender=gender,
            document_type=document_type,
            document_number=document_number,
            issuing_country=issuing_country,
            nationality=nationality,
            expiry_date=expiry_date,
            name_number=name_number,
            received_from=received_from,
        )

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
            retrieve_status = application_results_status(retrieve_root)
            if retrieve_status != "Complete":
                detail = "; ".join(
                    application_result_signals(retrieve_root)
                ) or "sin detalle"
                raise SabreSoapSecureFlightDocsError(
                    "TravelItineraryReadRQ no completó: "
                    f"status={retrieve_status or '-'}; {detail}"
                )

            segment_count = count_flight_segments(retrieve_root)
            if segment_count != expected_segment_count:
                raise SabreSoapSecureFlightDocsError(
                    "El PNR no coincide con el Booking congelado: "
                    f"segments={segment_count}, expected={expected_segment_count}."
                )

            try:
                transport = self._call(
                    session,
                    action="PassengerDetailsRQ",
                    service="PassengerDetailsRQ",
                    body=body,
                )
            except Exception as exc:
                self._close_best_effort(session)
                raise SabreSoapSecureFlightDocsReconciliationRequiredError(
                    "PassengerDetailsRQ tuvo resultado de transporte ambiguo. "
                    "NO RETRY; reconciliar el PNR antes de otro write."
                ) from exc

            try:
                root = _parse_xml(
                    transport,
                    action="PassengerDetailsRQ",
                )
            except Exception as exc:
                self._close_best_effort(session)
                raise SabreSoapSecureFlightDocsReconciliationRequiredError(
                    "PassengerDetailsRQ devolvió una respuesta no verificable. "
                    "NO RETRY; reconciliar el PNR antes de otro write."
                ) from exc

            status = application_results_status(root)
            if status != "Complete":
                detail = "; ".join(
                    application_result_signals(root)
                ) or "sin detalle"
                self._close_best_effort(session)
                raise SabreSoapSecureFlightDocsReconciliationRequiredError(
                    "PassengerDetailsRQ no quedó inequívocamente Complete: "
                    f"status={status or '-'}; {detail}. "
                    "NO RETRY; reconciliar el PNR antes de otro write."
                )

            close_ok = self._close_best_effort(session)
            return SabreSoapSecureFlightDocsResult(
                application_status=status,
                flight_segment_count=segment_count,
                session_close_ok=close_ok,
            )

        except SabreSoapSecureFlightDocsReconciliationRequiredError:
            raise
        except Exception:
            self._close_best_effort(session)
            raise
