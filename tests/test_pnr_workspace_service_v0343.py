from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.booking import (
    BookingContactRecord,
    BookingOfferRevision,
    BookingOfferSnapshot,
    BookingOfferSource,
    BookingPassengerRecord,
    BookingPassengersResponse,
    BookingRecord,
    BookingStatus,
    PnrAttemptStatus,
    RevalidationStatus,
)
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FlightSegment
from app.models.pnr_workspace import (
    PnrContact,
    PnrNextActionCode,
    PnrPassenger,
    PnrPricingCoverageStatus,
    PnrPricingSelectionStatus,
    PnrSegment,
    PnrSnapshot,
    PnrWorkspaceSnapshotRecord,
    PnrWorkspaceStatus,
)
from app.models.quote_request import PassengerKind, PassengerSpec
from app.services.pnr_workspace_service import (
    PnrWorkspaceService,
    PnrWorkspaceStateError,
)


def _booking(
    *,
    status: BookingStatus = BookingStatus.PNR_CREATED,
) -> BookingRecord:
    segment = FlightSegment(
        marketing_carrier="AA",
        operating_carrier="AA",
        flight_number="900",
        departure_airport="EZE",
        arrival_airport="MIA",
        departure_at=datetime(2026, 9, 19, 20, 45),
        arrival_at=datetime(2026, 9, 20, 4, 55),
        booking_class="S",
        cabin_code="Y",
    )
    fare = CommercialFare(
        cabin="ECONOMY",
        currency="USD",
        brand_name="MAIN CABIN FLEXIBLE",
        brand_code="MAINFL",
        price_per_passenger=Decimal("781.33"),
        total_price=Decimal("781.33"),
        fare_basis_codes=["SNL0ABC"],
        validating_carrier="AA",
    )
    offer = BookingOfferRevision(
        offer_revision_id=1,
        booking_id="B-TEST",
        revision_number=2,
        source=BookingOfferSource.REVALIDATION,
        snapshot=BookingOfferSnapshot(
            source_quote_id="Q-TEST",
            rank=1,
            fare_index=0,
            segments=[segment],
            fare=fare,
            passenger_mix=[
                PassengerSpec(
                    type=PassengerKind.ADULT,
                    quantity=1,
                )
            ],
        ),
        created_at="2026-09-01T12:00:00+00:00",
        accepted_at="2026-09-01T12:00:00+00:00",
    )
    return BookingRecord(
        booking_id="B-TEST",
        source_quote_id="Q-TEST",
        selected_rank=1,
        environment="cert",
        status=status,
        revalidation_status=RevalidationStatus.MATCHED,
        accepted_offer_revision_id=1,
        revision=4,
        client_request_id="11111111-1111-1111-1111-111111111111",
        created_at="2026-09-01T11:00:00+00:00",
        updated_at="2026-09-01T12:05:00+00:00",
        accepted_offer_revision=offer,
    )


def _passengers() -> BookingPassengersResponse:
    return BookingPassengersResponse(
        booking_id="B-TEST",
        booking_revision=4,
        complete=True,
        passengers=[
            BookingPassengerRecord(
                slot_index=1,
                passenger_type=PassengerKind.ADULT,
                given_name="TEST",
                surname="PASSENGER",
                date_of_birth=date(1980, 1, 1),
                gender="M",
                complete=True,
            )
        ],
    )


def _contact() -> BookingContactRecord:
    return BookingContactRecord(
        booking_id="B-TEST",
        booking_revision=4,
        name="TEST PASSENGER",
        email="test@example.com",
        phone_country_code="+54",
        phone_number="1155551234",
        complete=True,
    )


def _snapshot() -> PnrSnapshot:
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        passengers=[
            PnrPassenger(
                name_number="01.01",
                passenger_type="ADT",
            )
        ],
        segments=[
            PnrSegment(
                segment_number="1",
                marketing_carrier="AA",
                operating_carrier="AA",
                flight_number="900",
                origin="EZE",
                destination="MIA",
                departure_at="2026-09-19T20:45",
                arrival_at="2026-09-20T04:55",
                booking_class="S",
                status="HK",
                number_in_party=1,
            )
        ],
        contacts=[
            PnrContact(
                kind="email",
                value="test@example.com",
            ),
            PnrContact(
                kind="phone",
                value="+541155551234",
            ),
        ],
    )


class _BookingRepository:
    def __init__(self, booking: BookingRecord | None) -> None:
        self.booking = booking

    def get(self, booking_id: str):
        if self.booking is None:
            return None
        assert booking_id == self.booking.booking_id
        return self.booking


class _AttemptService:
    def __init__(self, confirmation_id: str = "OVFOTM") -> None:
        self.confirmation_id = confirmation_id

    def get(self, booking_id: str):
        return SimpleNamespace(
            booking_id=booking_id,
            status=PnrAttemptStatus.SUCCEEDED,
            confirmation_id=self.confirmation_id,
        )


class _ValueService:
    def __init__(self, value) -> None:
        self.value = value

    def get(self, booking_id: str):
        assert booking_id == "B-TEST"
        return self.value


class _SnapshotRepository:
    def __init__(
        self,
        cached: PnrWorkspaceSnapshotRecord | None = None,
    ) -> None:
        self.cached = cached
        self.saved = 0

    def latest(self, booking_id: str):
        assert booking_id == "B-TEST"
        return self.cached

    def save(
        self,
        *,
        booking_id: str,
        confirmation_id: str,
        provider: str,
        environment: str,
        snapshot: PnrSnapshot,
    ):
        self.saved += 1
        self.cached = PnrWorkspaceSnapshotRecord(
            booking_id=booking_id,
            confirmation_id=confirmation_id,
            provider=provider,
            environment=environment,
            retrieved_at="2026-09-01T20:00:00+00:00",
            snapshot=snapshot,
        )
        return self.cached


class _Reader:
    def __init__(
        self,
        *,
        snapshot: PnrSnapshot | None = None,
        error: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error

    def retrieve(self, confirmation_id: str):
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return SimpleNamespace(
            confirmation_id=confirmation_id,
            snapshot=self.snapshot,
        )


def _service(
    *,
    booking: BookingRecord | None = None,
    reader=None,
    snapshot_repository: _SnapshotRepository | None = None,
    read_attempts: int = 1,
    backoff_seconds: float = 0.0,
    sleeper=None,
) -> PnrWorkspaceService:
    return PnrWorkspaceService(
        booking_repository=_BookingRepository(
            booking or _booking()
        ),
        attempt_service=_AttemptService(),
        passenger_service=_ValueService(_passengers()),
        contact_service=_ValueService(_contact()),
        snapshot_repository=(
            snapshot_repository or _SnapshotRepository()
        ),
        settings_loader=lambda environment: object(),
        reader_factory=lambda settings: (
            reader or _Reader(snapshot=_snapshot())
        ),
        read_attempts=read_attempts,
        backoff_seconds=backoff_seconds,
        sleeper=sleeper or (lambda _: None),
    )


def test_successful_sync_persists_and_assesses_real_pnr() -> None:
    snapshots = _SnapshotRepository()
    response = _service(
        snapshot_repository=snapshots
    ).get("B-TEST")

    assert snapshots.saved == 1
    assert response.status == PnrWorkspaceStatus.NEEDS_ATTENTION
    assert response.stale is False
    assert response.snapshot is not None
    assert response.assessment is not None
    assert response.next_action is not None
    assert response.next_action.code == (
        PnrNextActionCode.STORE_OR_VERIFY_PRICING
    )
    assert response.pricing_selection is not None
    assert response.pricing_selection.status == PnrPricingSelectionStatus.MISSING
    assert response.pricing_coverage is not None
    assert response.pricing_coverage.status == PnrPricingCoverageStatus.UNKNOWN
    assert response.read_error_code is None


def test_read_failure_without_cache_keeps_create_success_separate() -> None:
    response = _service(
        reader=_Reader(error=RuntimeError("provider down"))
    ).get("B-TEST")

    assert response.status == PnrWorkspaceStatus.READ_ERROR
    assert response.confirmation_id == "OVFOTM"
    assert response.snapshot is None
    assert response.assessment is None
    assert response.next_action is None
    assert response.stale is False
    assert response.read_error_code == "PNR_READ_FAILED"


def test_read_failure_returns_last_valid_snapshot_as_stale() -> None:
    cached = PnrWorkspaceSnapshotRecord(
        booking_id="B-TEST",
        confirmation_id="OVFOTM",
        provider="sabre_travel_itinerary_read",
        environment="cert",
        retrieved_at="2026-09-01T19:00:00+00:00",
        snapshot=_snapshot(),
    )
    response = _service(
        reader=_Reader(error=RuntimeError("provider down")),
        snapshot_repository=_SnapshotRepository(cached),
    ).get("B-TEST")

    assert response.status == PnrWorkspaceStatus.READ_ERROR
    assert response.stale is True
    assert response.snapshot is not None
    assert response.assessment is not None
    assert response.next_action is not None
    assert response.next_action.code == (
        PnrNextActionCode.STORE_OR_VERIFY_PRICING
    )


def test_workspace_rejects_booking_before_pnr_created() -> None:
    service = _service(
        booking=_booking(
            status=BookingStatus.READY_TO_CREATE_PNR
        )
    )

    with pytest.raises(PnrWorkspaceStateError):
        service.get("B-TEST")

class _SequenceReader:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def retrieve(self, confirmation_id: str):
        index = min(self.calls, len(self.outcomes) - 1)
        outcome = self.outcomes[index]
        self.calls += 1

        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, PnrSnapshot):
            return SimpleNamespace(
                confirmation_id=outcome.confirmation_id,
                snapshot=outcome,
            )
        return outcome


def test_transient_read_errors_retry_then_persist_success() -> None:
    delays: list[float] = []
    reader = _SequenceReader(
        [
            RuntimeError("temporary 1"),
            RuntimeError("temporary 2"),
            _snapshot(),
        ]
    )
    snapshots = _SnapshotRepository()

    response = _service(
        reader=reader,
        snapshot_repository=snapshots,
        read_attempts=4,
        backoff_seconds=0.5,
        sleeper=delays.append,
    ).get("B-TEST")

    assert reader.calls == 3
    assert delays == [0.5, 1.0]
    assert snapshots.saved == 1
    assert response.read_error_code is None
    assert response.stale is False


def test_incomplete_segment_shape_retries_before_persisting() -> None:
    delays: list[float] = []
    incomplete = _snapshot().model_copy(
        update={"segments": []}
    )
    reader = _SequenceReader([incomplete, _snapshot()])
    snapshots = _SnapshotRepository()

    response = _service(
        reader=reader,
        snapshot_repository=snapshots,
        read_attempts=4,
        backoff_seconds=0.5,
        sleeper=delays.append,
    ).get("B-TEST")

    assert reader.calls == 2
    assert delays == [0.5]
    assert snapshots.saved == 1
    assert response.snapshot is not None
    assert len(response.snapshot.segments) == 1


def test_persistent_valid_segment_mismatch_is_real_assessment_data() -> None:
    incomplete = _snapshot().model_copy(
        update={"segments": []}
    )
    reader = _SequenceReader([incomplete] * 4)
    snapshots = _SnapshotRepository()

    response = _service(
        reader=reader,
        snapshot_repository=snapshots,
        read_attempts=4,
    ).get("B-TEST")

    assert reader.calls == 4
    assert snapshots.saved == 1
    assert response.status == PnrWorkspaceStatus.NEEDS_ATTENTION
    assert response.read_error_code is None
    assert response.next_action is not None
    assert response.next_action.code == PnrNextActionCode.REVIEW_ITINERARY


def test_retry_exhaustion_returns_safe_read_error() -> None:
    delays: list[float] = []
    reader = _SequenceReader(
        [RuntimeError("provider secret detail")] * 4
    )

    response = _service(
        reader=reader,
        read_attempts=4,
        backoff_seconds=0.5,
        sleeper=delays.append,
    ).get("B-TEST")

    assert reader.calls == 4
    assert delays == [0.5, 1.0, 2.0]
    assert response.status == PnrWorkspaceStatus.READ_ERROR
    assert response.read_error_code == "PNR_READ_FAILED"
    assert "provider secret detail" not in (
        response.read_error_message or ""
    )


def test_locator_mismatch_is_not_retried() -> None:
    delays: list[float] = []
    mismatched = _snapshot().model_copy(
        update={"confirmation_id": "ABCDEF"}
    )
    reader = _SequenceReader([mismatched])
    snapshots = _SnapshotRepository()

    response = _service(
        reader=reader,
        snapshot_repository=snapshots,
        read_attempts=4,
        backoff_seconds=0.5,
        sleeper=delays.append,
    ).get("B-TEST")

    assert reader.calls == 1
    assert delays == []
    assert snapshots.saved == 0
    assert response.status == PnrWorkspaceStatus.READ_ERROR
    assert response.read_error_code == "PNR_LOCATOR_MISMATCH"


def test_incomplete_then_errors_preserves_previous_snapshot_as_stale() -> None:
    incomplete = _snapshot().model_copy(
        update={"segments": []}
    )
    cached = PnrWorkspaceSnapshotRecord(
        booking_id="B-TEST",
        confirmation_id="OVFOTM",
        provider="sabre_travel_itinerary_read",
        environment="cert",
        retrieved_at="2026-09-01T19:00:00+00:00",
        snapshot=_snapshot(),
    )
    snapshots = _SnapshotRepository(cached)
    reader = _SequenceReader(
        [
            incomplete,
            RuntimeError("temporary 1"),
            RuntimeError("temporary 2"),
            RuntimeError("temporary 3"),
        ]
    )

    response = _service(
        reader=reader,
        snapshot_repository=snapshots,
        read_attempts=4,
    ).get("B-TEST")

    assert reader.calls == 4
    assert snapshots.saved == 0
    assert response.status == PnrWorkspaceStatus.READ_ERROR
    assert response.stale is True
    assert response.snapshot is not None
    assert len(response.snapshot.segments) == 1
