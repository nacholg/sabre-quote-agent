from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.booking import (
    BookingOfferRevision,
    BookingOfferSnapshot,
    BookingOfferSource,
    BookingRecord,
    BookingStatus,
    RevalidationStatus,
)
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.models.pnr_workspace import (
    PnrAssessment,
    PnrAssessmentCheck,
    PnrCheckStatus,
    PnrPriceQuote,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrPurchaseDeadline,
    PnrPurchaseDeadlineStatus,
    PnrSameBrandRequoteStatus,
    PnrWorkspaceResponse,
    PnrWorkspaceStatus,
)
from app.models.quote_request import PassengerKind, PassengerSpec, SearchLeg
from app.sabre.revalidation import SabreRevalidationResult
from app.services.pnr_same_brand_requote_service import (
    PnrSameBrandRequoteService,
)


def _segment() -> FlightSegment:
    return FlightSegment(
        marketing_carrier="AA",
        operating_carrier="AA",
        flight_number="900",
        departure_airport="EZE",
        arrival_airport="MIA",
        departure_at=datetime(2026, 9, 19, 20, 45),
        arrival_at=datetime(2026, 9, 20, 4, 55),
        booking_class="S",
    )


def _booking() -> BookingRecord:
    snapshot = BookingOfferSnapshot(
        source_quote_id="Q-1",
        rank=1,
        fare_index=0,
        segments=[_segment()],
        fare=CommercialFare(
            cabin="economy",
            currency="USD",
            brand_code="MAINFL",
            brand_name="MAIN CABIN FLEXIBLE",
            price_per_passenger=Decimal("781.33"),
            total_price=Decimal("781.33"),
            fare_basis_codes=["OLD/L040"],
            validating_carrier="AA",
        ),
        passenger_mix=[
            PassengerSpec(type=PassengerKind.ADULT, quantity=1)
        ],
        legs=[
            SearchLeg(
                origin="EZE",
                destination="MIA",
                departure_date=date(2026, 9, 19),
            )
        ],
    )
    revision = BookingOfferRevision(
        offer_revision_id=1,
        booking_id="B-1",
        revision_number=1,
        source=BookingOfferSource.INITIAL,
        snapshot=snapshot,
        created_at="2026-09-01T10:00:00+00:00",
        accepted_at="2026-09-01T10:00:00+00:00",
    )
    return BookingRecord(
        booking_id="B-1",
        source_quote_id="Q-1",
        selected_rank=1,
        environment="cert",
        status=BookingStatus.PNR_CREATED,
        revalidation_status=RevalidationStatus.MATCHED,
        accepted_offer_revision_id=1,
        revision=1,
        client_request_id="00000000-0000-0000-0000-000000000001",
        created_at="2026-09-01T10:00:00+00:00",
        updated_at="2026-09-01T10:00:00+00:00",
        accepted_offer_revision=revision,
    )


def _workspace(*, itin_changed=True, expired=True):
    checks = [
        PnrAssessmentCheck(
            code="SEGMENTS_MATCH",
            label="segments",
            status=PnrCheckStatus.PASS,
            blocking=True,
        ),
        PnrAssessmentCheck(
            code="SEGMENTS_CONFIRMED",
            label="confirmed",
            status=PnrCheckStatus.PASS,
            blocking=True,
        ),
    ]
    return PnrWorkspaceResponse(
        booking_id="B-1",
        confirmation_id="OVFOTM",
        provider="tir",
        environment="cert",
        status=PnrWorkspaceStatus.NEEDS_ATTENTION,
        stale=False,
        assessment=PnrAssessment(
            status=PnrWorkspaceStatus.NEEDS_ATTENTION,
            checks=checks,
        ),
        pricing_selection=PnrPricingSelection(
            status=PnrPricingSelectionStatus.SELECTED,
            candidates=[
                PnrPriceQuote(
                    record_number="1",
                    status="ACTIVE",
                    itinerary_changed=itin_changed,
                )
            ],
            total_quote_count=1,
            candidate_quote_count=1,
            candidate_record_numbers=["1"],
        ),
        purchase_deadline=PnrPurchaseDeadline(
            status=(
                PnrPurchaseDeadlineStatus.EXPIRED
                if expired
                else PnrPurchaseDeadlineStatus.RESOLVED
            )
        ),
    )


class FakeRepo:
    def get(self, booking_id):
        return _booking() if booking_id == "B-1" else None


class FakeWorkspace:
    def __init__(self, value):
        self.value = value

    def get(self, booking_id):
        return self.value


class FakeProvider:
    provider_name = "fake_revalidate"

    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def revalidate(self, snapshot, legs, *, environment):
        self.calls += 1
        return self.result


def _result(*, brand="MAINFL", total="808.13"):
    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal(total),
        total_price=Decimal(total),
        brand_code=brand,
        brand_name="MAIN CABIN FLEXIBLE",
        fare_basis_codes=["SLN7AHM5/L040"],
        validating_carrier="AA",
        last_ticket_date="2026-09-05",
    )
    option = ItineraryOption(
        segments=[_segment()],
        fare=fare,
        fare_options_by_currency={"USD": [fare]},
    )
    return SabreRevalidationResult(
        options=[option],
        transaction_id="TX-1",
        no_availability=False,
        messages=[],
    )


@pytest.mark.asyncio
async def test_same_brand_found_is_read_only_candidate():
    provider = FakeProvider(_result())
    service = PnrSameBrandRequoteService(
        booking_repository=FakeRepo(),
        workspace_service=FakeWorkspace(_workspace()),
        provider=provider,
    )

    response = await service.refresh("B-1")

    assert response.status == PnrSameBrandRequoteStatus.FOUND
    assert response.read_only is True
    assert response.candidate_brand_code == "MAINFL"
    assert response.candidate_total == Decimal("808.13")
    assert response.price_difference == Decimal("26.80")
    assert response.candidate_fare_basis_codes == ["SLN7AHM5/L040"]
    assert response.candidate_last_ticket_date == "2026-09-05"
    assert response.trigger_reasons == [
        "PQ_ITINERARY_CHANGED",
        "PURCHASE_DEADLINE_EXPIRED",
    ]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_different_brand_is_not_accepted():
    provider = FakeProvider(_result(brand="MAIN"))
    service = PnrSameBrandRequoteService(
        booking_repository=FakeRepo(),
        workspace_service=FakeWorkspace(_workspace()),
        provider=provider,
    )

    response = await service.refresh("B-1")

    assert (
        response.status
        == PnrSameBrandRequoteStatus.SAME_BRAND_UNAVAILABLE
    )
    assert response.candidate_total is None


@pytest.mark.asyncio
async def test_no_stale_reason_skips_bfm():
    provider = FakeProvider(_result())
    service = PnrSameBrandRequoteService(
        booking_repository=FakeRepo(),
        workspace_service=FakeWorkspace(
            _workspace(itin_changed=False, expired=False)
        ),
        provider=provider,
    )

    response = await service.refresh("B-1")

    assert response.status == PnrSameBrandRequoteStatus.NOT_REQUIRED
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_unconfirmed_segments_block_automatic_requote():
    workspace = _workspace()
    workspace.assessment.checks[1].status = PnrCheckStatus.FAIL
    provider = FakeProvider(_result())
    service = PnrSameBrandRequoteService(
        booking_repository=FakeRepo(),
        workspace_service=FakeWorkspace(workspace),
        provider=provider,
    )

    response = await service.refresh("B-1")

    assert response.status == PnrSameBrandRequoteStatus.BLOCKED
    assert "PNR_SEGMENTS_NOT_CONFIRMED" in response.blockers
    assert provider.calls == 0
