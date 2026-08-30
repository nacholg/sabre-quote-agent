from __future__ import annotations

from dataclasses import dataclass
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


class SabreSoapSecureFlightError(RuntimeError):
    pass


class SabreSoapSecureFlightReconciliationRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class SabreSoapSecureFlightResult:
    application_status: str
    flight_segment_count: int
    session_close_ok: bool


def _xml_attr(value: str) -> str:
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _xml_text(value: str) -> str:
    return escape(str(value))


def build_secure_flight_body(
    confirmation_id: str,
    *,
    given_name: str,
    surname: str,
    date_of_birth: str,
    gender: str,
    name_number: str = "1.1",
    received_from: str = "SABRE QUOTE AGENT",
) -> str:
    locator = _xml_attr(confirmation_id.strip().upper())
    given = _xml_text(given_name.strip().upper())
    family = _xml_text(surname.strip().upper())
    dob = _xml_attr(date_of_birth.strip())
    sex = _xml_attr(gender.strip().upper())
    number = _xml_attr(name_number.strip())
    received = _xml_attr(received_from.strip() or "SABRE QUOTE AGENT")

    if not locator or not given or not family or not dob or not sex or not number:
        raise SabreSoapSecureFlightError(
            "Secure Flight requiere locator, nombre, apellido, DOB, género y NameNumber."
        )
    if sex not in {"M", "F", "X"}:
        raise SabreSoapSecureFlightError(
            "Secure Flight adulto sólo admite M, F o X en este harness."
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
            <SecureFlight SegmentNumber="A">
              <PersonName
                  DateOfBirth="{dob}"
                  Gender="{sex}"
                  NameNumber="{number}">
                <GivenName>{given}</GivenName>
                <Surname>{family}</Surname>
              </PersonName>
            </SecureFlight>
          </SpecialServiceInfo>
        </SpecialServiceRQ>
      </SpecialReqDetails>
    </PassengerDetailsRQ>"""


class SabreSoapSecureFlightService:
    """CERT-only experimental SFPD write using PassengerDetailsRQ.

    No automatic retry exists. PassengerDetailsRQ includes EndTransaction;
    therefore any ambiguous transport/result after submission must be reconciled
    in Sabre before another write.
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
        expected_segment_count: int,
        name_number: str = "1.1",
        received_from: str = "SABRE QUOTE AGENT",
    ) -> SabreSoapSecureFlightResult:
        if self.settings.sabre_env.strip().upper() != "CERT":
            raise SabreSoapSecureFlightError(
                "Este workflow experimental sólo permite Sabre CERT."
            )
        if not self.settings.sabre_secure_flight_enabled:
            raise SabreSoapSecureFlightError(
                "SABRE_SECURE_FLIGHT_ENABLED debe ser true para este write."
            )

        # Validate the complete write payload before opening a Sabre session.
        body = build_secure_flight_body(
            confirmation_id,
            given_name=given_name,
            surname=surname,
            date_of_birth=date_of_birth,
            gender=gender,
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
                raise SabreSoapSecureFlightError(
                    "TravelItineraryReadRQ no completó: "
                    f"status={retrieve_status or '-'}; {detail}"
                )

            segment_count = count_flight_segments(retrieve_root)
            if segment_count != expected_segment_count:
                raise SabreSoapSecureFlightError(
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
                raise SabreSoapSecureFlightReconciliationRequiredError(
                    "PassengerDetailsRQ tuvo resultado de transporte ambiguo. "
                    "NO RETRY; verificar *P3D en Sabre."
                ) from exc

            try:
                root = _parse_xml(
                    transport,
                    action="PassengerDetailsRQ",
                )
            except Exception as exc:
                self._close_best_effort(session)
                raise SabreSoapSecureFlightReconciliationRequiredError(
                    "PassengerDetailsRQ devolvió una respuesta no verificable. "
                    "NO RETRY; verificar *P3D en Sabre."
                ) from exc

            status = application_results_status(root)
            if status != "Complete":
                detail = "; ".join(
                    application_result_signals(root)
                ) or "sin detalle"
                self._close_best_effort(session)
                raise SabreSoapSecureFlightReconciliationRequiredError(
                    "PassengerDetailsRQ no quedó inequívocamente Complete: "
                    f"status={status or '-'}; {detail}. "
                    "NO RETRY; verificar *P3D en Sabre."
                )

            close_ok = self._close_best_effort(session)
            return SabreSoapSecureFlightResult(
                application_status=status,
                flight_segment_count=segment_count,
                session_close_ok=close_ok,
            )

        except SabreSoapSecureFlightReconciliationRequiredError:
            raise
        except Exception:
            self._close_best_effort(session)
            raise
