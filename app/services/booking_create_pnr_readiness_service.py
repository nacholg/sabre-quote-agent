from __future__ import annotations

from sqlalchemy import select

from app.db.models import BookingRevalidationRow
from app.models.booking import (
    BookingCreatePnrReadinessResponse,
    BookingOfferSnapshot,
    BookingOfferSource,
    BookingRecord,
    BookingStatus,
    RevalidationStatus,
)
from app.models.quote_request import PassengerKind
from app.services.booking_readiness_service import (
    contact_complete,
    passengers_complete,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


REVALIDATION_TABLE = BookingRevalidationRow.__table__


def sabre_create_booking_passenger_code(
    passenger_type: PassengerKind,
) -> str:
    """Map our canonical passenger type to Booking Management Create Booking."""
    if passenger_type == PassengerKind.ADULT:
        return "ADT"
    if passenger_type == PassengerKind.CHILD:
        # Booking Management traditional-air examples use CNN.
        return "CNN"
    if passenger_type == PassengerKind.INFANT:
        return "INF"
    raise ValueError(
        f"Tipo de pasajero no soportado para Create Booking: {passenger_type}."
    )


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _latest_revalidation_row(
    repository: BookingRepository,
    booking_id: str,
):
    with repository.engine.connect() as connection:
        return (
            connection.execute(
                select(REVALIDATION_TABLE)
                .where(
                    REVALIDATION_TABLE.c.booking_id == booking_id
                )
                .order_by(
                    REVALIDATION_TABLE.c.revalidation_id.desc()
                )
                .limit(1)
            )
            .mappings()
            .first()
        )


def _snapshot_gate(
    snapshot: BookingOfferSnapshot,
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []

    # v0.32 must never recover trip shape from mutable Shopping state.
    if not snapshot.legs:
        _append_once(reasons, "offer_not_self_contained")

    if not snapshot.segments:
        _append_once(reasons, "offer_has_no_segments")
        return reasons, warnings

    for segment in snapshot.segments:
        if not (
            str(segment.marketing_carrier or "").strip()
            and str(segment.flight_number or "").strip()
            and str(segment.departure_airport or "").strip()
            and str(segment.arrival_airport or "").strip()
            and str(segment.booking_class or "").strip()
        ):
            _append_once(reasons, "segment_booking_data_incomplete")

        if not str(segment.flight_number or "").strip().isdigit():
            _append_once(reasons, "segment_flight_number_invalid")

    if not str(snapshot.fare.currency or "").strip():
        _append_once(reasons, "fare_currency_missing")

    # Not a blocker for basic booking, but important to flight pricing.
    if not str(snapshot.fare.validating_carrier or "").strip():
        _append_once(warnings, "validating_carrier_missing")

    selected_brand = str(
        snapshot.fare.brand_code
        or snapshot.fare.brand_name
        or ""
    ).strip()
    if selected_brand and not snapshot.fare.branded_components:
        _append_once(
            warnings,
            "branded_fare_component_metadata_missing",
        )

    # Current FlightSegment does not persist isMarriageGroup. Surface this
    # explicitly for connections instead of silently inventing false in v0.32.
    if snapshot.legs and len(snapshot.segments) > len(snapshot.legs):
        _append_once(
            warnings,
            "marriage_group_metadata_not_captured",
        )

    # Identity documents/APIS are not required by the simplest traditional-air
    # Create Booking samples, but are relevant later for international PNRs.
    if any(
        segment.departure_country
        and segment.arrival_country
        and segment.departure_country != segment.arrival_country
        for segment in snapshot.segments
    ):
        _append_once(
            warnings,
            "identity_documents_not_captured",
        )

    return reasons, warnings


class BookingCreatePnrReadinessService:
    """Pure read-side gate. It never calls Sabre and never mutates Booking."""

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )

    def _booking(self, booking_id: str) -> BookingRecord:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)
        return booking

    def get(
        self,
        booking_id: str,
    ) -> BookingCreatePnrReadinessResponse:
        booking = self._booking(booking_id)
        reasons: list[str] = []
        warnings: list[str] = []

        if booking.status != BookingStatus.READY_TO_CREATE_PNR:
            _append_once(reasons, "booking_status_not_ready")

        if booking.revalidation_status != RevalidationStatus.MATCHED:
            _append_once(
                reasons,
                "booking_revalidation_not_matched",
            )

        revision = booking.accepted_offer_revision
        snapshot = revision.snapshot if revision is not None else None

        if revision is None:
            _append_once(reasons, "accepted_offer_missing")
        else:
            if revision.source != BookingOfferSource.REVALIDATION:
                _append_once(
                    reasons,
                    "accepted_offer_not_revalidated",
                )

            snapshot_reasons, snapshot_warnings = _snapshot_gate(
                revision.snapshot
            )
            for item in snapshot_reasons:
                _append_once(reasons, item)
            for item in snapshot_warnings:
                _append_once(warnings, item)

        if not passengers_complete(
            self.booking_repository,
            booking,
        ):
            _append_once(reasons, "passengers_incomplete")

        if not contact_complete(
            self.booking_repository,
            booking.booking_id,
        ):
            _append_once(reasons, "contact_incomplete")

        row = _latest_revalidation_row(
            self.booking_repository,
            booking.booking_id,
        )
        revalidation_id: int | None = None
        if row is None:
            _append_once(reasons, "revalidation_missing")
        else:
            revalidation_id = int(row["revalidation_id"])

            if row["status"] != RevalidationStatus.MATCHED.value:
                _append_once(
                    reasons,
                    "latest_revalidation_not_matched",
                )

            if row["stale_at"] is not None:
                _append_once(reasons, "revalidation_stale")

            if (
                booking.accepted_offer_revision_id is None
                or row["candidate_offer_revision_id"]
                != booking.accepted_offer_revision_id
            ):
                _append_once(
                    reasons,
                    "accepted_offer_not_latest_matched_candidate",
                )

        passenger_count = (
            sum(item.quantity for item in snapshot.passenger_mix)
            if snapshot is not None
            else 0
        )
        segment_count = (
            len(snapshot.segments)
            if snapshot is not None
            else 0
        )

        sabre_codes: list[str] = []
        if snapshot is not None:
            try:
                sabre_codes = [
                    sabre_create_booking_passenger_code(item.type)
                    for item in snapshot.passenger_mix
                ]
            except ValueError:
                _append_once(
                    reasons,
                    "unsupported_passenger_type",
                )

        return BookingCreatePnrReadinessResponse(
            booking_id=booking.booking_id,
            booking_revision=booking.revision,
            ready=not reasons,
            status=booking.status,
            revalidation_status=booking.revalidation_status,
            accepted_offer_revision_id=(
                booking.accepted_offer_revision_id
            ),
            revalidation_id=revalidation_id,
            passenger_count=passenger_count,
            segment_count=segment_count,
            sabre_passenger_codes=sabre_codes,
            reasons=reasons,
            warnings=warnings,
        )


def get_booking_create_pnr_readiness_service(
) -> BookingCreatePnrReadinessService:
    return BookingCreatePnrReadinessService()
