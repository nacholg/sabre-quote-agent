from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
import re

from app.models.booking import (
    BookingContactRecord,
    BookingPassengersResponse,
    BookingRecord,
)
from app.models.pnr_workspace import (
    PnrAssessment,
    PnrAssessmentCheck,
    PnrAssessmentResult,
    PnrCheckStatus,
    PnrNextAction,
    PnrNextActionCode,
    PnrPricingCoverage,
    PnrPricingCoverageStatus,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrSnapshot,
    PnrWorkspaceStatus,
)
from app.services.pnr_pricing_coverage_service import assess_pnr_pricing_coverage
from app.services.pnr_pricing_selection_service import select_pnr_pricing


_UNRESOLVED = {PnrCheckStatus.FAIL, PnrCheckStatus.UNKNOWN}


def _upper(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


def _flight_number(value: str | None) -> str | None:
    normalized = _upper(value)
    return str(int(normalized)) if normalized and normalized.isdigit() else normalized


def _minute(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text[:16]
    return parsed.replace(
        tzinfo=None,
        second=0,
        microsecond=0,
    ).isoformat(timespec="minutes")


def _digits(value: str | None) -> str | None:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits or None


def _pax_kind(value: str | None) -> str | None:
    normalized = _upper(value)
    if normalized in {"ADT", "INF"}:
        return normalized
    if normalized in {"CHILD", "CNN", "CHD"} or (
        normalized is not None and re.fullmatch(r"C\d{2}", normalized)
    ):
        return "CHILD"
    return normalized


def _counter_text(counter: Counter[str]) -> str:
    return ",".join(
        f"{key}:{counter[key]}"
        for key in sorted(counter)
    ) or "-"


def _check(
    code: str,
    label: str,
    status: PnrCheckStatus,
    *,
    blocking: bool = False,
    expected: str | None = None,
    actual: str | None = None,
    message: str | None = None,
) -> PnrAssessmentCheck:
    return PnrAssessmentCheck(
        code=code,
        label=label,
        status=status,
        blocking=blocking,
        expected=expected,
        actual=actual,
        message=message,
    )


class PnrAssessmentService:
    """Interpret a normalized Sabre PNR against the frozen Booking."""

    def assess(
        self,
        *,
        booking: BookingRecord,
        passengers: BookingPassengersResponse,
        contact: BookingContactRecord,
        snapshot: PnrSnapshot,
    ) -> PnrAssessmentResult:
        revision = booking.accepted_offer_revision
        if revision is None:
            raise ValueError("Booking sin oferta aceptada para assessment.")

        pricing_selection = select_pnr_pricing(snapshot)
        pricing_coverage = assess_pnr_pricing_coverage(snapshot, pricing_selection)
        checks = [
            *self._segment_checks(revision.snapshot.segments, snapshot),
            *self._passenger_checks(passengers, snapshot),
            *self._contact_checks(contact, snapshot),
            *self._pricing_checks(
                revision.snapshot.fare,
                snapshot,
                pricing_selection,
                pricing_coverage,
            ),
            *self._ticketing_checks(snapshot),
        ]
        blocking = [
            item
            for item in checks
            if item.blocking and item.status in _UNRESOLVED
        ]
        assessment = PnrAssessment(
            status=(
                PnrWorkspaceStatus.NEEDS_ATTENTION
                if blocking
                else PnrWorkspaceStatus.READY_FOR_TICKETING
            ),
            checks=checks,
            warnings=[
                item.code
                for item in checks
                if item.status == PnrCheckStatus.WARN
            ],
            unknowns=[
                item.code
                for item in checks
                if item.status == PnrCheckStatus.UNKNOWN
            ],
            errors=[item.code for item in blocking],
        )
        return PnrAssessmentResult(
            assessment=assessment,
            next_action=self._next_action(checks),
            pricing_selection=pricing_selection,
            pricing_coverage=pricing_coverage,
        )

    @staticmethod
    def _segment_checks(expected_segments, snapshot: PnrSnapshot):
        actual_segments = snapshot.segments
        mismatches: list[str] = []
        if len(expected_segments) != len(actual_segments):
            mismatches.append("segment_count")
        else:
            for index, (expected, actual) in enumerate(
                zip(expected_segments, actual_segments),
                start=1,
            ):
                expected_key = (
                    _upper(expected.marketing_carrier),
                    _flight_number(expected.flight_number),
                    _upper(expected.departure_airport),
                    _upper(expected.arrival_airport),
                    _minute(expected.departure_at),
                    _minute(expected.arrival_at),
                    _upper(expected.booking_class),
                )
                actual_key = (
                    _upper(actual.marketing_carrier),
                    _flight_number(actual.flight_number),
                    _upper(actual.origin),
                    _upper(actual.destination),
                    _minute(actual.departure_at),
                    _minute(actual.arrival_at),
                    _upper(actual.booking_class),
                )
                if expected_key != actual_key:
                    mismatches.append(f"segment_{index}")

        statuses = [_upper(item.status) or "-" for item in actual_segments]
        confirmed = bool(actual_segments) and all(
            value == "HK"
            for value in statuses
        )
        return [
            _check(
                "SEGMENTS_MATCH",
                "Itinerario coincide",
                (
                    PnrCheckStatus.PASS
                    if not mismatches
                    else PnrCheckStatus.FAIL
                ),
                blocking=True,
                expected=f"{len(expected_segments)} segmento(s)",
                actual=f"{len(actual_segments)} segmento(s)",
                message=(
                    None
                    if not mismatches
                    else "No coincide: " + ", ".join(mismatches)
                ),
            ),
            _check(
                "SEGMENTS_CONFIRMED",
                "Segmentos confirmados",
                (
                    PnrCheckStatus.PASS
                    if confirmed
                    else PnrCheckStatus.FAIL
                ),
                blocking=True,
                expected="HK",
                actual=",".join(statuses) if statuses else "-",
            ),
        ]

    @staticmethod
    def _passenger_checks(
        passengers: BookingPassengersResponse,
        snapshot: PnrSnapshot,
    ):
        expected_count = len(passengers.passengers)
        actual_count = len(snapshot.passengers)
        expected_types = Counter(
            kind
            for kind in (
                _pax_kind(item.passenger_type.value)
                for item in passengers.passengers
            )
            if kind
        )
        actual_values = [
            _pax_kind(item.passenger_type)
            for item in snapshot.passengers
        ]
        actual_types = Counter(
            value
            for value in actual_values
            if value
        )
        if any(value is None for value in actual_values):
            type_status = PnrCheckStatus.UNKNOWN
        else:
            type_status = (
                PnrCheckStatus.PASS
                if actual_types == expected_types
                else PnrCheckStatus.FAIL
            )
        return [
            _check(
                "PASSENGER_COUNT_MATCH",
                "Cantidad de pasajeros",
                (
                    PnrCheckStatus.PASS
                    if expected_count == actual_count
                    else PnrCheckStatus.FAIL
                ),
                blocking=True,
                expected=str(expected_count),
                actual=str(actual_count),
            ),
            _check(
                "PASSENGER_TYPES_MATCH",
                "Tipos de pasajero",
                type_status,
                blocking=(type_status == PnrCheckStatus.FAIL),
                expected=_counter_text(expected_types),
                actual=(
                    _counter_text(actual_types)
                    if len(actual_types) == actual_count
                    else "no verificable"
                ),
                message=(
                    "Sabre no devolvió PassengerType para todos los nombres."
                    if type_status == PnrCheckStatus.UNKNOWN
                    else None
                ),
            ),
        ]

    @staticmethod
    def _contact_checks(
        contact: BookingContactRecord,
        snapshot: PnrSnapshot,
    ):
        emails = {
            item.value.strip().lower()
            for item in snapshot.contacts
            if item.kind == "email" and item.value.strip()
        }
        phones = {
            normalized
            for normalized in (
                _digits(item.value)
                for item in snapshot.contacts
                if item.kind == "phone"
            )
            if normalized
        }
        has_both = bool(emails) and bool(phones)
        expected_email = (
            contact.email.strip().lower()
            if contact.email and contact.email.strip()
            else None
        )
        expected_phone = _digits(
            f"{contact.phone_country_code or ''}"
            f"{contact.phone_number or ''}"
        )

        if expected_email is None or expected_phone is None:
            match_status = PnrCheckStatus.UNKNOWN
            match_message = (
                "El Booking local no conserva contacto completo."
            )
        elif expected_email in emails and expected_phone in phones:
            match_status = PnrCheckStatus.PASS
            match_message = None
        elif has_both:
            match_status = PnrCheckStatus.WARN
            match_message = (
                "Sabre conserva contacto, pero difiere del contacto congelado."
            )
        else:
            match_status = PnrCheckStatus.UNKNOWN
            match_message = "No hay contacto suficiente para comparar."

        return [
            _check(
                "CONTACT_PRESENT",
                "Contacto presente",
                (
                    PnrCheckStatus.PASS
                    if has_both
                    else PnrCheckStatus.FAIL
                ),
                blocking=True,
                expected="email+phone",
                actual=(
                    "email+phone"
                    if has_both
                    else (
                        "email"
                        if emails
                        else "phone"
                        if phones
                        else "-"
                    )
                ),
            ),
            _check(
                "CONTACT_MATCH",
                "Contacto coincide",
                match_status,
                blocking=False,
                expected="contacto congelado",
                actual="contacto Sabre",
                message=match_message,
            ),
        ]

    @staticmethod
    def _pricing_checks(
        fare,
        snapshot: PnrSnapshot,
        selection: PnrPricingSelection,
        coverage: PnrPricingCoverage,
    ):
        quotes = snapshot.price_quotes
        present = bool(quotes)
        candidates = selection.candidates
        selected = (
            selection.status == PnrPricingSelectionStatus.SELECTED
            and bool(candidates)
        )

        if not present:
            candidate_status = PnrCheckStatus.UNKNOWN
            candidate_blocking = False
        elif selected:
            candidate_status = PnrCheckStatus.PASS
            candidate_blocking = True
        else:
            candidate_status = PnrCheckStatus.FAIL
            candidate_blocking = True

        expected_currency = _upper(fare.currency)
        currency_values = [
            _upper(item.total_currency)
            for item in candidates
        ]
        currencies = {
            value
            for value in currency_values
            if value
        }
        if (
            not selected
            or expected_currency is None
            or any(value is None for value in currency_values)
        ):
            currency_status = PnrCheckStatus.UNKNOWN
        else:
            currency_status = (
                PnrCheckStatus.PASS
                if currencies == {expected_currency}
                else PnrCheckStatus.FAIL
            )

        totals = [item.total_amount for item in candidates]
        total: Decimal | None = None
        if (
            not selected
            or fare.total_price is None
            or any(value is None for value in totals)
        ):
            price_status = PnrCheckStatus.UNKNOWN
        else:
            total = sum(
                (value for value in totals if value is not None),
                Decimal("0"),
            )
            price_status = (
                PnrCheckStatus.PASS
                if total == fare.total_price
                else PnrCheckStatus.FAIL
            )

        expected_carrier = _upper(fare.validating_carrier)
        carrier_values = [
            _upper(item.validating_carrier)
            for item in candidates
        ]
        carriers = {value for value in carrier_values if value}
        if expected_carrier is None:
            carrier_status = PnrCheckStatus.UNKNOWN
            carrier_blocking = False
        elif not selected:
            carrier_status = PnrCheckStatus.UNKNOWN
            carrier_blocking = False
        elif any(value is None for value in carrier_values):
            carrier_status = PnrCheckStatus.UNKNOWN
            carrier_blocking = True
        else:
            carrier_status = (
                PnrCheckStatus.PASS
                if carriers == {expected_carrier}
                else PnrCheckStatus.FAIL
            )
            carrier_blocking = True

        if selected:
            active_actual = (
                f"{selection.candidate_quote_count} ACTIVE / "
                f"{selection.total_quote_count} total"
            )
        elif present:
            active_actual = (
                f"0 ACTIVE / {selection.total_quote_count} total"
            )
        else:
            active_actual = "sin PQ"

        brand = _upper(fare.brand_code) or fare.brand_name
        return [
            _check(
                "PRICING_PRESENT",
                "Tarifa almacenada",
                PnrCheckStatus.PASS if present else PnrCheckStatus.FAIL,
                blocking=True,
                expected="PQ presente",
                actual=f"{len(quotes)} PQ" if present else "sin PQ",
            ),
            _check(
                "ACTIVE_PRICING_SELECTED",
                "Pricing activo identificado",
                candidate_status,
                blocking=candidate_blocking,
                expected="PQ status ACTIVE",
                actual=active_actual,
                message=selection.message,
            ),
            _check(
                "PRICING_PASSENGER_COVERAGE",
                "Cobertura de pasajeros por pricing",
                (
                    PnrCheckStatus.PASS
                    if coverage.status == PnrPricingCoverageStatus.EXACT
                    else (
                        PnrCheckStatus.UNKNOWN
                        if coverage.status == PnrPricingCoverageStatus.UNKNOWN
                        else PnrCheckStatus.FAIL
                    )
                ),
                blocking=(selection.status == PnrPricingSelectionStatus.SELECTED),
                expected="cada pasajero cubierto exactamente 1 vez",
                actual=(f"{coverage.covered_passenger_count}/{coverage.passenger_count} cubiertos"),
                message=coverage.message,
            ),
            _check(
                "CURRENCY_MATCH",
                "Moneda coincide",
                currency_status,
                blocking=selected,
                expected=expected_currency or "-",
                actual=",".join(sorted(currencies)) or "-",
            ),
            _check(
                "PRICE_MATCH",
                "Total coincide",
                price_status,
                blocking=selected,
                expected=(str(fare.total_price) if fare.total_price is not None else "-"),
                actual=str(total) if total is not None else "-",
            ),
            _check(
                "VALIDATING_CARRIER_MATCH",
                "Validating carrier coincide",
                carrier_status,
                blocking=carrier_blocking,
                expected=expected_carrier or "-",
                actual=",".join(sorted(carriers)) or "-",
            ),
            _check(
                "BRAND_MATCH",
                "Brand verificable",
                PnrCheckStatus.UNKNOWN,
                blocking=False,
                expected=brand or "-",
                actual="-",
                message=(
                    "La ausencia de BrandID en TravelItineraryReadRS "
                    "no se trata como mismatch."
                ),
            ),
        ]

    @staticmethod
    def _ticketing_checks(snapshot: PnrSnapshot):
        ticketing = snapshot.ticketing
        advisory_text = (
            ":".join(
                value
                for value in (
                    ticketing.advisory_airline_code,
                    ticketing.advisory_code,
                    ticketing.advisory_status,
                )
                if value
            )
            if ticketing.advisory_present
            else "-"
        )
        return [
            _check(
                "TICKETING_ADVISORY",
                "Aviso de ticketing",
                (
                    PnrCheckStatus.WARN
                    if ticketing.advisory_present
                    else PnrCheckStatus.PASS
                ),
                blocking=False,
                expected="sin aviso bloqueante",
                actual=advisory_text,
                message=(
                    "Sabre devolvió un advisory de ticketing."
                    if ticketing.advisory_present
                    else None
                ),
            ),
            _check(
                "TICKETING_DEADLINE",
                "Deadline de emisión",
                (
                    PnrCheckStatus.PASS
                    if ticketing.deadline_at
                    else PnrCheckStatus.UNKNOWN
                ),
                blocking=False,
                expected="deadline estructurado",
                actual=ticketing.deadline_at or "-",
                message=(
                    None
                    if ticketing.deadline_at
                    else (
                        "No se detectó un deadline estructurado; "
                        "no se inventa."
                    )
                ),
            ),
        ]

    @staticmethod
    def _next_action(
        checks: list[PnrAssessmentCheck],
    ) -> PnrNextAction:
        by_code = {item.code: item for item in checks}

        def unresolved(code: str) -> bool:
            item = by_code[code]
            return (
                item.blocking
                and item.status in _UNRESOLVED
            )

        if (
            unresolved("SEGMENTS_MATCH")
            or unresolved("SEGMENTS_CONFIRMED")
        ):
            code, label = (
                PnrNextActionCode.REVIEW_ITINERARY,
                "Revisar itinerario en Sabre.",
            )
        elif (
            unresolved("PASSENGER_COUNT_MATCH")
            or unresolved("PASSENGER_TYPES_MATCH")
        ):
            code, label = (
                PnrNextActionCode.REVIEW_PASSENGERS,
                "Revisar pasajeros en Sabre.",
            )
        elif unresolved("CONTACT_PRESENT"):
            code, label = (
                PnrNextActionCode.REVIEW_CONTACT,
                "Completar/revisar contacto.",
            )
        elif unresolved("PRICING_PRESENT"):
            code, label = (
                PnrNextActionCode.STORE_OR_VERIFY_PRICING,
                "Guardar/verificar tarifa en Sabre.",
            )
        elif unresolved("ACTIVE_PRICING_SELECTED"):
            code, label = (
                PnrNextActionCode.REVIEW_PRICING,
                "Revisar cuál pricing ACTIVE corresponde emitir.",
            )
        elif unresolved("PRICING_PASSENGER_COVERAGE"):
            code, label = (
                PnrNextActionCode.REVIEW_PRICING,
                "Revisar cobertura de pasajeros por las PQ ACTIVE.",
            )
        elif any(
            unresolved(name)
            for name in (
                "CURRENCY_MATCH",
                "PRICE_MATCH",
                "VALIDATING_CARRIER_MATCH",
            )
        ):
            code, label = (
                PnrNextActionCode.REVIEW_PRICING,
                "Revisar pricing almacenado.",
            )
        else:
            code, label = (
                PnrNextActionCode.ISSUE_TICKET,
                "La reserva está lista para emitir.",
            )
        return PnrNextAction(
            code=code,
            label=label,
        )
