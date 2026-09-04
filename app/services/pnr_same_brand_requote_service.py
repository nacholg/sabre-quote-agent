from __future__ import annotations

from decimal import Decimal

from app.models.booking import BookingStatus
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.models.pnr_workspace import (
    PnrCheckStatus,
    PnrPurchaseDeadlineStatus,
    PnrSameBrandRequoteResponse,
    PnrSameBrandRequoteStatus,
)
from app.sabre.revalidation import (
    SabreRevalidationProvider,
    SabreRevalidationResult,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)
from app.services.pnr_workspace_service import (
    PnrWorkspaceService,
    get_pnr_workspace_service,
)


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def _flight_number(value: object) -> str:
    raw = str(value or "").strip()
    return str(int(raw)) if raw.isdigit() else raw.upper()


def _segment_key(segment: FlightSegment) -> tuple:
    return (
        _norm(segment.marketing_carrier),
        _norm(segment.operating_carrier or segment.marketing_carrier),
        _flight_number(segment.flight_number),
        _norm(segment.departure_airport),
        _norm(segment.arrival_airport),
        segment.departure_at.replace(
            tzinfo=None,
            second=0,
            microsecond=0,
        ).isoformat(),
        segment.arrival_at.replace(
            tzinfo=None,
            second=0,
            microsecond=0,
        ).isoformat(),
        _norm(segment.booking_class),
    )


def _fare_total(fare: FareOption) -> Decimal:
    value = (
        fare.total_price
        if fare.total_price is not None
        else fare.price_per_passenger
    )
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _all_fares(option: ItineraryOption) -> list[FareOption]:
    result: list[FareOption] = []
    seen: set[int] = set()

    for fares in (option.fare_options_by_currency or {}).values():
        for fare in fares:
            marker = id(fare)
            if marker not in seen:
                seen.add(marker)
                result.append(fare)

    if not result:
        for fare in (option.fares_by_currency or {}).values():
            marker = id(fare)
            if marker not in seen:
                seen.add(marker)
                result.append(fare)

    if not result:
        result.append(option.fare)

    return result


def _check_passed(workspace, code: str) -> bool:
    if workspace.assessment is None:
        return False
    for check in workspace.assessment.checks:
        if check.code == code:
            return check.status == PnrCheckStatus.PASS
    return False


class PnrSameBrandRequoteService:
    """Read-only same-brand fare refresh for a created PNR.

    This service never modifies the PNR, never stores a PQ and never changes
    the accepted Booking offer. It only produces a fresh candidate price.
    """

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        workspace_service: PnrWorkspaceService | None = None,
        provider: SabreRevalidationProvider | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )
        self.workspace_service = (
            workspace_service or get_pnr_workspace_service()
        )
        self.provider = provider or SabreRevalidationProvider()

    async def refresh(
        self,
        booking_id: str,
    ) -> PnrSameBrandRequoteResponse:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)
        if booking.status != BookingStatus.PNR_CREATED:
            raise ValueError(
                "La recotización post-PNR requiere un Booking PNR_CREATED."
            )
        revision = booking.accepted_offer_revision
        if revision is None:
            raise ValueError("El Booking no tiene oferta aceptada.")

        workspace = self.workspace_service.get(booking_id)
        response = PnrSameBrandRequoteResponse(
            booking_id=booking.booking_id,
            confirmation_id=workspace.confirmation_id,
            status=PnrSameBrandRequoteStatus.BLOCKED,
            source_brand_code=revision.snapshot.fare.brand_code,
            source_brand_name=revision.snapshot.fare.brand_name,
            source_currency=revision.snapshot.fare.currency,
            source_total=(
                revision.snapshot.fare.total_price
                if revision.snapshot.fare.total_price is not None
                else revision.snapshot.fare.price_per_passenger
            ),
        )

        if workspace.stale or workspace.read_error_code:
            response.blockers.append("FRESH_PNR_READ_REQUIRED")
            response.message = (
                "Se requiere una lectura fresca del PNR antes de recotizar."
            )
            return response

        trigger_reasons: list[str] = []
        selection = workspace.pricing_selection
        if selection is not None and any(
            quote.itinerary_changed is True
            for quote in selection.candidates
        ):
            trigger_reasons.append("PQ_ITINERARY_CHANGED")

        if (
            workspace.purchase_deadline is not None
            and workspace.purchase_deadline.status
            == PnrPurchaseDeadlineStatus.EXPIRED
        ):
            trigger_reasons.append("PURCHASE_DEADLINE_EXPIRED")

        response.trigger_reasons = trigger_reasons

        if not trigger_reasons:
            response.status = PnrSameBrandRequoteStatus.NOT_REQUIRED
            response.message = "El pricing actual no requiere recotización."
            return response

        if not _check_passed(workspace, "SEGMENTS_MATCH"):
            response.blockers.append("PNR_ITINERARY_MISMATCH")
        if not _check_passed(workspace, "SEGMENTS_CONFIRMED"):
            response.blockers.append("PNR_SEGMENTS_NOT_CONFIRMED")
        if response.blockers:
            response.message = (
                "No se recotiza automáticamente mientras el itinerario "
                "actual no coincida y esté confirmado."
            )
            return response

        source_brand_code = _norm(revision.snapshot.fare.brand_code)
        if not source_brand_code:
            response.blockers.append("EXACT_BRAND_CODE_REQUIRED")
            response.message = (
                "No hay un brand_code Sabre exacto para preservar la brand."
            )
            return response

        if not revision.snapshot.legs:
            response.blockers.append("BOOKING_LEGS_REQUIRED")
            response.message = (
                "El Booking no tiene legs congelados para recotización exacta."
            )
            return response

        result: SabreRevalidationResult = await self.provider.revalidate(
            revision.snapshot,
            list(revision.snapshot.legs),
            environment=booking.environment,
        )
        response.provider = self.provider.provider_name
        response.provider_reference = result.transaction_id

        source_segments = [
            _segment_key(segment)
            for segment in revision.snapshot.segments
        ]
        exact_options = [
            option
            for option in result.options
            if [_segment_key(segment) for segment in option.segments]
            == source_segments
        ]

        if not exact_options:
            response.status = (
                PnrSameBrandRequoteStatus.EXACT_ITINERARY_UNAVAILABLE
            )
            response.message = (
                "Sabre no devolvió el itinerario exacto reservado."
            )
            return response

        source_currency = _norm(revision.snapshot.fare.currency)
        candidates: list[FareOption] = []
        for option in exact_options:
            for fare in _all_fares(option):
                if (
                    _norm(fare.brand_code) == source_brand_code
                    and _norm(fare.currency) == source_currency
                ):
                    candidates.append(fare)

        if not candidates:
            response.status = (
                PnrSameBrandRequoteStatus.SAME_BRAND_UNAVAILABLE
            )
            response.message = (
                "El itinerario existe, pero la branded fare original "
                "ya no está disponible."
            )
            return response

        best = min(candidates, key=_fare_total)
        new_total = _fare_total(best)
        old_total = Decimal(str(response.source_total)).quantize(
            Decimal("0.01")
        )

        response.status = PnrSameBrandRequoteStatus.FOUND
        response.candidate_brand_code = best.brand_code
        response.candidate_brand_name = best.brand_name
        response.candidate_currency = best.currency
        response.candidate_total = new_total
        response.price_difference = new_total - old_total
        response.candidate_fare_basis_codes = list(
            best.fare_basis_codes or []
        )
        response.candidate_last_ticket_date = best.last_ticket_date
        response.message = (
            "Sabre devolvió una nueva tarifa para el itinerario exacto "
            "y la misma branded fare. No se modificó el PNR."
        )
        return response


def get_pnr_same_brand_requote_service() -> PnrSameBrandRequoteService:
    return PnrSameBrandRequoteService()
