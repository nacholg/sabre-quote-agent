from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from sqlalchemy import func, insert, select, update

from app.db.models import (
    BookingOfferRevisionRow,
    BookingRevalidationRow,
    BookingRow,
)
from app.models.booking import (
    BookingOfferSnapshot,
    BookingOfferSource,
    BookingRecord,
    BookingRevalidationRequest,
    BookingRevalidationResponse,
    BookingStatus,
    RevalidationStatus,
)
from app.models.commercial_quote import (
    CommercialFare,
    CommercialPassengerPrice,
)
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.models.quote_request import QuoteSearchRequest, SearchLeg
from app.sabre.revalidation import (
    RevalidationRequestError,
    SabreRevalidationProvider,
    SabreRevalidationResult,
)
from app.services.booking_readiness_service import (
    contact_complete,
    passengers_complete,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)
from app.services.booking_state import require_transition
from app.services.quote_repository import (
    QuoteRepository,
    get_quote_repository,
)


BOOKING_TABLE = BookingRow.__table__
OFFER_REVISION_TABLE = BookingOfferRevisionRow.__table__
REVALIDATION_TABLE = BookingRevalidationRow.__table__


class BookingRevalidationProvider(Protocol):
    provider_name: str

    async def revalidate(
        self,
        snapshot: BookingOfferSnapshot,
        legs: list[SearchLeg],
        *,
        environment: str,
    ) -> SabreRevalidationResult:
        ...


class BookingRevalidationConflictError(RuntimeError):
    """Booking changed while Revalidate was in progress."""


class BookingRevalidationStateError(RuntimeError):
    """Booking is not eligible to enter the Revalidation phase."""


class BookingRevalidationDataError(ValueError):
    """Frozen Booking data is insufficient or inconsistent."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _wall_clock(value: datetime) -> str:
    return value.replace(tzinfo=None).isoformat(timespec="minutes")


def _normalized_flight_number(value: str) -> str:
    raw = str(value).strip()
    if raw.isdigit():
        return str(int(raw))
    return raw.upper()


def _segment_signature(segment: FlightSegment) -> tuple:
    return (
        segment.marketing_carrier.upper(),
        (
            segment.operating_carrier
            or segment.marketing_carrier
        ).upper(),
        _normalized_flight_number(segment.flight_number),
        segment.departure_airport.upper(),
        segment.arrival_airport.upper(),
        _wall_clock(segment.departure_at),
        _wall_clock(segment.arrival_at),
        (segment.booking_class or "").upper(),
    )


def _fare_basis(value: list[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(item).strip().upper()
                for item in value
                if str(item).strip()
            }
        )
    )


def _money(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _source_total(fare: CommercialFare) -> Decimal:
    return _money(
        fare.total_price
        if fare.total_price is not None
        else fare.price_per_passenger
    ) or Decimal("0.00")


def _candidate_total(fare: FareOption) -> Decimal:
    return _money(
        fare.total_price
        if fare.total_price is not None
        else fare.price_per_passenger
    ) or Decimal("0.00")


def _itinerary_distance(
    source: list[FlightSegment],
    candidate: list[FlightSegment],
) -> int:
    if len(source) != len(candidate):
        return abs(len(source) - len(candidate)) + 100

    return sum(
        1
        for left, right in zip(source, candidate)
        if _segment_signature(left) != _segment_signature(right)
    )


def _fare_candidates(option: ItineraryOption) -> list[FareOption]:
    result: list[FareOption] = []
    seen: set[int] = set()

    for fares in (option.fare_options_by_currency or {}).values():
        for fare in fares:
            marker = id(fare)
            if marker not in seen:
                seen.add(marker)
                result.append(fare)

    if not result:
        result.extend((option.fares_by_currency or {}).values())
    if not result:
        result.append(option.fare)
    return result


def _candidate_fare_score(
    source: CommercialFare,
    candidate: FareOption,
) -> tuple:
    source_basis = _fare_basis(source.fare_basis_codes)
    candidate_basis = _fare_basis(candidate.fare_basis_codes)

    source_brand = (
        source.brand_code or source.brand_name or ""
    ).strip().upper()
    candidate_brand = (
        candidate.brand_code or candidate.brand_name or ""
    ).strip().upper()

    return (
        0 if candidate.currency.upper() == source.currency.upper() else 1,
        0 if candidate.cabin.lower() == source.cabin.lower() else 1,
        0 if source_basis and candidate_basis == source_basis else (
            0 if not source_basis else 1
        ),
        0 if (
            not source_brand
            or not candidate_brand
            or source_brand == candidate_brand
        ) else 1,
        abs(_candidate_total(candidate) - _source_total(source)),
    )


def _choose_candidate(
    source: BookingOfferSnapshot,
    options: list[ItineraryOption],
) -> tuple[ItineraryOption, FareOption]:
    if not options:
        raise BookingRevalidationDataError(
            "Sabre no devolvió opciones normalizadas para comparar."
        )

    option = min(
        options,
        key=lambda item: _itinerary_distance(
            source.segments,
            item.segments,
        ),
    )
    fares = _fare_candidates(option)
    fare = min(
        fares,
        key=lambda item: _candidate_fare_score(
            source.fare,
            item,
        ),
    )
    return option, fare


def _commercial_passenger_prices(
    fare: FareOption,
) -> list[CommercialPassengerPrice]:
    return [
        CommercialPassengerPrice(
            passenger_type=item.passenger_type,
            quantity=item.quantity,
            age=item.age,
            currency=item.currency,
            unit_price=item.unit_price,
            total_price=item.total_price,
            q1_amount=item.q1_amount,
            q1_total=item.q1_total,
            q1_currency=item.q1_currency,
        )
        for item in fare.passenger_prices
    ]


def _commercial_candidate_fare(
    source: CommercialFare,
    fare: FareOption,
) -> CommercialFare:
    return CommercialFare(
        cabin=fare.cabin,
        currency=fare.currency,
        brand_name=fare.brand_name,
        brand_code=fare.brand_code,
        price_per_passenger=fare.price_per_passenger,
        total_price=fare.total_price,
        passenger_prices=_commercial_passenger_prices(fare),
        fare_basis_codes=list(fare.fare_basis_codes or []),
        validating_carrier=fare.validating_carrier,
        q1_amount=fare.q1_amount,
        q1_currency=fare.q1_currency,
        # Revalidate verifies availability/pricing. Existing commercial rules
        # remain attached until a later Air Rules refresh explicitly replaces them.
        rules=source.rules,
    )


def _fare_identity_changes(
    source: CommercialFare,
    candidate: CommercialFare,
) -> list[dict[str, str | None]]:
    changes: list[dict[str, str | None]] = []

    def add(field: str, before, after) -> None:
        changes.append(
            {
                "field": field,
                "before": None if before is None else str(before),
                "after": None if after is None else str(after),
            }
        )

    if source.currency.upper() != candidate.currency.upper():
        add("currency", source.currency, candidate.currency)

    if source.cabin.lower() != candidate.cabin.lower():
        add("cabin", source.cabin, candidate.cabin)

    source_basis = _fare_basis(source.fare_basis_codes)
    candidate_basis = _fare_basis(candidate.fare_basis_codes)
    if source_basis != candidate_basis:
        add(
            "fare_basis_codes",
            " / ".join(source_basis),
            " / ".join(candidate_basis),
        )

    if (
        source.validating_carrier
        and candidate.validating_carrier
        and source.validating_carrier.upper()
        != candidate.validating_carrier.upper()
    ):
        add(
            "validating_carrier",
            source.validating_carrier,
            candidate.validating_carrier,
        )

    source_brand = source.brand_code or source.brand_name
    candidate_brand = candidate.brand_code or candidate.brand_name
    if (
        source_brand
        and candidate_brand
        and source_brand.strip().upper()
        != candidate_brand.strip().upper()
    ):
        add("brand", source_brand, candidate_brand)

    return changes


def compare_revalidated_offer(
    source: BookingOfferSnapshot,
    option: ItineraryOption,
    fare: FareOption,
) -> tuple[RevalidationStatus, BookingOfferSnapshot, dict[str, object]]:
    candidate_fare = _commercial_candidate_fare(source.fare, fare)
    candidate = BookingOfferSnapshot(
        source_quote_id=source.source_quote_id,
        rank=source.rank,
        fare_index=source.fare_index,
        segments=option.segments,
        fare=candidate_fare,
        passenger_mix=source.passenger_mix,
        legs=source.legs,
    )

    source_signatures = [
        _segment_signature(item)
        for item in source.segments
    ]
    candidate_signatures = [
        _segment_signature(item)
        for item in candidate.segments
    ]

    diff: dict[str, object] = {
        "source": {
            "currency": source.fare.currency,
            "total_price": str(_source_total(source.fare)),
            "fare_basis_codes": list(source.fare.fare_basis_codes),
            "brand": source.fare.brand_code or source.fare.brand_name,
        },
        "candidate": {
            "currency": candidate.fare.currency,
            "total_price": str(_source_total(candidate.fare)),
            "fare_basis_codes": list(candidate.fare.fare_basis_codes),
            "brand": (
                candidate.fare.brand_code
                or candidate.fare.brand_name
            ),
        },
        "changes": [],
    }

    if source_signatures != candidate_signatures:
        diff["changes"] = [
            {
                "field": "itinerary",
                "before": source_signatures,
                "after": candidate_signatures,
            }
        ]
        return (
            RevalidationStatus.ITINERARY_CHANGED,
            candidate,
            diff,
        )

    fare_changes = _fare_identity_changes(
        source.fare,
        candidate.fare,
    )
    if fare_changes:
        diff["changes"] = fare_changes
        return RevalidationStatus.FARE_CHANGED, candidate, diff

    if _source_total(source.fare) != _source_total(candidate.fare):
        diff["changes"] = [
            {
                "field": "total_price",
                "before": str(_source_total(source.fare)),
                "after": str(_source_total(candidate.fare)),
            }
        ]
        return RevalidationStatus.PRICE_CHANGED, candidate, diff

    return RevalidationStatus.MATCHED, candidate, diff


class BookingRevalidationService:
    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        quote_repository: QuoteRepository | None = None,
        provider: BookingRevalidationProvider | None = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )
        self.quote_repository = quote_repository
        self.provider = provider or SabreRevalidationProvider()

    def _booking(self, booking_id: str) -> BookingRecord:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)
        return booking

    def _legs(self, booking: BookingRecord) -> list[SearchLeg]:
        revision = booking.accepted_offer_revision
        if revision is None:
            raise BookingRevalidationDataError(
                "El Booking no tiene oferta aceptada."
            )

        if revision.snapshot.legs:
            return list(revision.snapshot.legs)

        # Compatibility for Bookings created before v0.31.4.
        repository = self.quote_repository or get_quote_repository()
        source_quote = repository.get(booking.source_quote_id)
        if source_quote is None:
            raise BookingRevalidationDataError(
                "El Booking histórico no tiene legs congelados y su Quote "
                "origen ya no está disponible."
            )

        try:
            search = QuoteSearchRequest.model_validate(
                source_quote.search_request
            )
        except Exception as exc:
            raise BookingRevalidationDataError(
                "No se pudieron reconstruir los legs del Quote origen."
            ) from exc
        return search.effective_legs()

    def _preflight(
        self,
        booking: BookingRecord,
        request: BookingRevalidationRequest,
    ) -> None:
        allowed = {
            BookingStatus.READY_FOR_REVIEW,
            BookingStatus.REVALIDATION_REQUIRED,
            BookingStatus.REQUIRES_AGENT_ACTION,
            BookingStatus.READY_TO_CREATE_PNR,
        }
        if booking.status not in allowed:
            raise BookingRevalidationStateError(
                f"El Booking {booking.status.value} no puede revalidarse."
            )

        if request.revision != booking.revision:
            raise BookingRevalidationConflictError(
                "El Booking cambió desde que abriste Review. "
                f"Recargá antes de revalidar (actual {booking.revision}, "
                f"recibida {request.revision})."
            )

        if not passengers_complete(
            self.booking_repository,
            booking,
        ):
            raise BookingRevalidationStateError(
                "No se puede revalidar con pasajeros incompletos."
            )
        if not contact_complete(
            self.booking_repository,
            booking.booking_id,
        ):
            raise BookingRevalidationStateError(
                "No se puede revalidar con contacto incompleto."
            )

        if booking.accepted_offer_revision is None:
            raise BookingRevalidationDataError(
                "El Booking no tiene una oferta aceptada para comparar."
            )

        require_transition(
            booking.status,
            BookingStatus.REVALIDATION_REQUIRED,
        )

    def _latest_row(self, booking_id: str):
        with self.booking_repository.engine.connect() as connection:
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

    @staticmethod
    def _response(
        booking: BookingRecord,
        row,
    ) -> BookingRevalidationResponse:
        if row is None:
            return BookingRevalidationResponse(
                booking_id=booking.booking_id,
                booking_revision=booking.revision,
                status=booking.status,
                revalidation_status=booking.revalidation_status,
            )

        return BookingRevalidationResponse(
            booking_id=booking.booking_id,
            booking_revision=booking.revision,
            status=booking.status,
            revalidation_status=booking.revalidation_status,
            revalidation_id=int(row["revalidation_id"]),
            checked_at=row["checked_at"],
            provider=row["provider"],
            provider_reference=row["provider_reference"],
            source_offer_revision_id=row["source_offer_revision_id"],
            candidate_offer_revision_id=row[
                "candidate_offer_revision_id"
            ],
            diff=(
                json.loads(row["diff_json"])
                if row["diff_json"]
                else None
            ),
            error_code=row["error_code"],
            error_message=row["error_message"],
            stale_at=row["stale_at"],
        )

    def get(self, booking_id: str) -> BookingRevalidationResponse:
        booking = self._booking(booking_id)
        return self._response(
            booking,
            self._latest_row(booking_id),
        )

    def _next_offer_revision_number(self, connection, booking_id: str) -> int:
        current = connection.execute(
            select(
                func.max(OFFER_REVISION_TABLE.c.revision_number)
            ).where(
                OFFER_REVISION_TABLE.c.booking_id == booking_id
            )
        ).scalar_one_or_none()
        return int(current or 0) + 1

    def _persist_result(
        self,
        booking: BookingRecord,
        *,
        request_revision: int,
        result_status: RevalidationStatus,
        provider_reference: str | None,
        candidate: BookingOfferSnapshot | None,
        diff: dict[str, object] | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> BookingRevalidationResponse:
        final_status = (
            BookingStatus.READY_TO_CREATE_PNR
            if result_status == RevalidationStatus.MATCHED
            else BookingStatus.REQUIRES_AGENT_ACTION
        )
        require_transition(
            BookingStatus.REVALIDATION_REQUIRED,
            final_status,
        )

        now = _utc_now()
        next_booking_revision = booking.revision + 1
        source_revision_id = booking.accepted_offer_revision_id

        with self.booking_repository.engine.begin() as connection:
            candidate_revision_id: int | None = None
            if candidate is not None:
                revision_number = self._next_offer_revision_number(
                    connection,
                    booking.booking_id,
                )
                accepted_at = (
                    now
                    if result_status == RevalidationStatus.MATCHED
                    else None
                )
                candidate_result = connection.execute(
                    insert(OFFER_REVISION_TABLE).values(
                        booking_id=booking.booking_id,
                        revision_number=revision_number,
                        source=BookingOfferSource.REVALIDATION.value,
                        snapshot_json=json.dumps(
                            candidate.model_dump(mode="json"),
                            ensure_ascii=False,
                        ),
                        created_at=now,
                        accepted_at=accepted_at,
                    )
                )
                candidate_revision_id = int(
                    candidate_result.inserted_primary_key[0]
                )

            revalidation_result = connection.execute(
                insert(REVALIDATION_TABLE).values(
                    booking_id=booking.booking_id,
                    provider=getattr(
                        self.provider,
                        "provider_name",
                        "revalidation_provider",
                    ),
                    status=result_status.value,
                    checked_at=now,
                    source_offer_revision_id=source_revision_id,
                    candidate_offer_revision_id=candidate_revision_id,
                    provider_reference=provider_reference,
                    diff_json=(
                        json.dumps(diff, ensure_ascii=False)
                        if diff is not None
                        else None
                    ),
                    error_code=error_code,
                    error_message=error_message,
                    stale_at=None,
                )
            )
            revalidation_id = int(
                revalidation_result.inserted_primary_key[0]
            )

            booking_values = {
                "status": final_status.value,
                "revalidation_status": result_status.value,
                "revision": next_booking_revision,
                "updated_at": now,
            }
            if (
                result_status == RevalidationStatus.MATCHED
                and candidate_revision_id is not None
            ):
                booking_values["accepted_offer_revision_id"] = (
                    candidate_revision_id
                )

            updated = connection.execute(
                update(BOOKING_TABLE)
                .where(
                    BOOKING_TABLE.c.booking_id == booking.booking_id,
                    BOOKING_TABLE.c.revision == request_revision,
                )
                .values(**booking_values)
            )
            if updated.rowcount != 1:
                raise BookingRevalidationConflictError(
                    "El Booking fue modificado mientras Sabre revalidaba. "
                    "El resultado fue descartado; recargá y repetí."
                )

        refreshed = self._booking(booking.booking_id)
        row = self._latest_row(booking.booking_id)
        if row is None or int(row["revalidation_id"]) != revalidation_id:
            raise RuntimeError(
                "No se pudo releer el resultado de revalidación."
            )
        return self._response(refreshed, row)

    async def revalidate(
        self,
        booking_id: str,
        request: BookingRevalidationRequest,
    ) -> BookingRevalidationResponse:
        booking = self._booking(booking_id)
        self._preflight(booking, request)

        source_revision = booking.accepted_offer_revision
        assert source_revision is not None
        source = source_revision.snapshot
        legs = self._legs(booking)

        try:
            provider_result = await self.provider.revalidate(
                source,
                legs,
                environment=booking.environment,
            )
        except RevalidationRequestError as exc:
            raise BookingRevalidationDataError(str(exc)) from exc
        except Exception as exc:
            return self._persist_result(
                booking,
                request_revision=request.revision,
                result_status=RevalidationStatus.ERROR,
                provider_reference=None,
                candidate=None,
                diff={
                    "changes": [],
                    "error": "provider_error",
                },
                error_code=exc.__class__.__name__,
                error_message=str(exc)[:1000],
            )

        if not provider_result.options:
            if provider_result.no_availability:
                return self._persist_result(
                    booking,
                    request_revision=request.revision,
                    result_status=RevalidationStatus.UNAVAILABLE,
                    provider_reference=provider_result.transaction_id,
                    candidate=None,
                    diff={
                        "changes": [
                            {
                                "field": "availability",
                                "before": "available",
                                "after": "unavailable",
                            }
                        ]
                    },
                )

            return self._persist_result(
                booking,
                request_revision=request.revision,
                result_status=RevalidationStatus.ERROR,
                provider_reference=provider_result.transaction_id,
                candidate=None,
                diff={
                    "changes": [],
                    "error": "empty_revalidation_response",
                },
                error_code="EMPTY_RESPONSE",
                error_message=(
                    "Sabre no devolvió una opción revalidada ni indicó "
                    "No Availability."
                ),
            )

        option, fare = _choose_candidate(
            source,
            provider_result.options,
        )
        result_status, candidate, diff = compare_revalidated_offer(
            source,
            option,
            fare,
        )
        if not candidate.legs:
            # Upgrade a pre-v0.31.4 Booking after its first successful
            # provider response so future revalidations are self-contained.
            candidate.legs = legs

        return self._persist_result(
            booking,
            request_revision=request.revision,
            result_status=result_status,
            provider_reference=provider_result.transaction_id,
            candidate=candidate,
            diff=diff,
        )


def get_booking_revalidation_service() -> BookingRevalidationService:
    return BookingRevalidationService()
