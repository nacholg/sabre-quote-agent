from __future__ import annotations

import re

from app.models.pnr_workspace import (
    PnrSecureFlightDocsCoverage,
    PnrSecureFlightDocsStatus,
    PnrSnapshot,
)


def _normalize_name_number(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.fullmatch(r"0*(\d+)\.0*(\d+)", raw)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2))}"
    return raw.upper()


def _status(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized or None


def assess_pnr_secure_flight_docs(
    snapshot: PnrSnapshot,
) -> PnrSecureFlightDocsCoverage:
    """Verify DOCS presence/status using normalized TIR metadata only.

    Raw SSR text and document numbers are intentionally not required or
    persisted. Coverage is proven from passenger NameNumber associations and
    DOCS status. Missing/ambiguous association fails closed.
    """

    passenger_count = len(snapshot.passengers)
    if passenger_count == 0:
        return PnrSecureFlightDocsCoverage(
            status=PnrSecureFlightDocsStatus.UNVERIFIED,
            passenger_count=0,
            blockers=["SECURE_FLIGHT_PASSENGERS_UNAVAILABLE"],
            message=(
                "No hay pasajeros normalizados para verificar DOCS/Secure Flight."
            ),
        )

    passenger_names: list[str] = []
    unnamed_passengers = 0
    for passenger in snapshot.passengers:
        normalized = _normalize_name_number(passenger.name_number)
        if normalized is None:
            unnamed_passengers += 1
        else:
            passenger_names.append(normalized)

    docs = [
        service
        for service in snapshot.special_services
        if str(service.code or "").strip().upper() == "DOCS"
    ]
    unassociated_docs = [
        service
        for service in docs
        if not [
            value
            for value in (
                _normalize_name_number(item)
                for item in service.name_numbers
            )
            if value
        ]
    ]

    covered: list[str] = []
    missing: list[str] = []
    unverified: list[str] = []

    for name_number in passenger_names:
        associated = []
        for service in docs:
            associations = {
                normalized
                for normalized in (
                    _normalize_name_number(item)
                    for item in service.name_numbers
                )
                if normalized
            }
            if name_number in associations:
                associated.append(service)

        if any(_status(service.status) == "HK" for service in associated):
            covered.append(name_number)
            continue

        if associated:
            # Explicit non-HK means the required DOCS is not confirmed.
            if any(_status(service.status) is None for service in associated):
                unverified.append(name_number)
            else:
                missing.append(name_number)
            continue

        # A DOCS entry exists but Sabre did not expose a passenger association:
        # never guess which passenger it belongs to.
        if unassociated_docs:
            unverified.append(name_number)
        else:
            missing.append(name_number)

    if unnamed_passengers:
        unverified.extend(
            f"unknown:{index}"
            for index in range(1, unnamed_passengers + 1)
        )

    covered = list(dict.fromkeys(covered))
    missing = list(dict.fromkeys(missing))
    unverified = list(dict.fromkeys(unverified))

    blockers: list[str] = []
    if missing:
        blockers.append("SECURE_FLIGHT_DOCS_MISSING")
    if unverified:
        blockers.append("SECURE_FLIGHT_DOCS_UNVERIFIED")

    if missing:
        status = PnrSecureFlightDocsStatus.MISSING
    elif unverified:
        status = PnrSecureFlightDocsStatus.UNVERIFIED
    else:
        status = PnrSecureFlightDocsStatus.COMPLETE

    if status == PnrSecureFlightDocsStatus.COMPLETE:
        message = (
            "DOCS/Secure Flight confirmado en HK y asociado a todos los pasajeros."
        )
    elif status == PnrSecureFlightDocsStatus.MISSING:
        message = (
            "Falta DOCS/Secure Flight confirmado para uno o más pasajeros."
        )
    else:
        message = (
            "DOCS/Secure Flight existe o puede existir, pero la asociación/estado "
            "no es verificable de forma inequívoca."
        )

    return PnrSecureFlightDocsCoverage(
        status=status,
        passenger_count=passenger_count,
        covered_name_numbers=covered,
        missing_name_numbers=missing,
        unverified_name_numbers=unverified,
        blockers=blockers,
        message=message,
    )
