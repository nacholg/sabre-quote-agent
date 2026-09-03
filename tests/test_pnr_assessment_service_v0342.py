from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.models.booking import (
    BookingContactRecord,
    BookingOfferRevision,
    BookingOfferSnapshot,
    BookingOfferSource,
    BookingPassengerRecord,
    BookingPassengersResponse,
    BookingRecord,
    BookingStatus,
    RevalidationStatus,
)
from app.models.commercial_quote import CommercialFare
from app.models.itinerary import FlightSegment
from app.models.pnr_workspace import (
    PnrCheckStatus,
    PnrContact,
    PnrNextActionCode,
    PnrPassenger,
    PnrPricingCoverageStatus,
    PnrPricingSelectionStatus,
    PnrPriceQuote,
    PnrSegment,
    PnrSnapshot,
    PnrTicketing,
    PnrWorkspaceStatus,
)
from app.models.quote_request import PassengerKind, PassengerSpec
from app.services.pnr_assessment_service import PnrAssessmentService


def _booking() -> BookingRecord:
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
        status=BookingStatus.PNR_CREATED,
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


def _snapshot(
    *,
    with_price: bool = True,
    price: Decimal = Decimal("781.33"),
    segment_class: str = "S",
    segment_status: str = "HK",
    email: str = "test@example.com",
    phone: str = "+541155551234",
    advisory: bool = False,
    passenger_type: str | None = "ADT",
) -> PnrSnapshot:
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        passengers=[
            PnrPassenger(
                name_number="01.01",
                passenger_type=passenger_type,
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
                booking_class=segment_class,
                status=segment_status,
                number_in_party=1,
            )
        ],
        contacts=[
            PnrContact(
                kind="email",
                value=email,
            ),
            PnrContact(
                kind="phone",
                value=phone,
            ),
        ],
        price_quotes=(
            [
                PnrPriceQuote(
                    record_number="1",
                    status="ACTIVE",
                    validating_carrier="AA",
                    passenger_type="ADT",
                    passenger_quantity=1,
                    passenger_name_numbers=["01.01"],
                    total_amount=price,
                    total_currency="USD",
                )
            ]
            if with_price
            else []
        ),
        ticketing=PnrTicketing(
            advisory_present=advisory,
            advisory_code="ADTK" if advisory else None,
            advisory_status="KK" if advisory else None,
            advisory_airline_code="1S" if advisory else None,
        ),
    )


def _result(snapshot: PnrSnapshot):
    return PnrAssessmentService().assess(
        booking=_booking(),
        passengers=_passengers(),
        contact=_contact(),
        snapshot=snapshot,
    )


def _check(result, code: str):
    return next(
        item
        for item in result.assessment.checks
        if item.code == code
    )


def test_real_cert_shape_without_pq_requires_pricing_action() -> None:
    result = _result(
        _snapshot(
            with_price=False,
            advisory=True,
        )
    )

    assert result.assessment.status == PnrWorkspaceStatus.NEEDS_ATTENTION
    assert _check(result, "SEGMENTS_MATCH").status == PnrCheckStatus.PASS
    assert _check(result, "SEGMENTS_CONFIRMED").status == PnrCheckStatus.PASS
    assert _check(result, "PASSENGER_COUNT_MATCH").status == PnrCheckStatus.PASS
    assert _check(result, "CONTACT_PRESENT").status == PnrCheckStatus.PASS
    assert _check(result, "PRICING_PRESENT").status == PnrCheckStatus.FAIL
    assert _check(result, "TICKETING_ADVISORY").status == PnrCheckStatus.WARN
    assert result.assessment.errors == ["PRICING_PRESENT"]
    assert "TICKETING_ADVISORY" in result.assessment.warnings
    assert result.next_action.code == (
        PnrNextActionCode.STORE_OR_VERIFY_PRICING
    )


def test_matching_pq_is_ready_for_ticketing() -> None:
    result = _result(_snapshot())

    assert result.assessment.status == (
        PnrWorkspaceStatus.READY_FOR_TICKETING
    )
    assert _check(result, "PRICING_PRESENT").status == PnrCheckStatus.PASS
    assert _check(result, "CURRENCY_MATCH").status == PnrCheckStatus.PASS
    assert _check(result, "PRICE_MATCH").status == PnrCheckStatus.PASS
    assert _check(result, "VALIDATING_CARRIER_MATCH").status == (
        PnrCheckStatus.PASS
    )
    assert _check(result, "BRAND_MATCH").status == PnrCheckStatus.UNKNOWN
    assert result.next_action.code == PnrNextActionCode.ISSUE_TICKET


def test_booking_class_mismatch_blocks_before_pricing_action() -> None:
    result = _result(
        _snapshot(
            segment_class="T",
        )
    )

    assert result.assessment.status == PnrWorkspaceStatus.NEEDS_ATTENTION
    assert _check(result, "SEGMENTS_MATCH").status == PnrCheckStatus.FAIL
    assert result.next_action.code == PnrNextActionCode.REVIEW_ITINERARY


def test_non_hk_segment_requires_itinerary_review() -> None:
    result = _result(
        _snapshot(
            segment_status="UC",
        )
    )

    assert _check(result, "SEGMENTS_CONFIRMED").status == PnrCheckStatus.FAIL
    assert result.next_action.code == PnrNextActionCode.REVIEW_ITINERARY


def test_price_mismatch_requires_pricing_review() -> None:
    result = _result(
        _snapshot(
            price=Decimal("800.00"),
        )
    )

    assert result.assessment.status == PnrWorkspaceStatus.NEEDS_ATTENTION
    assert _check(result, "PRICE_MATCH").status == PnrCheckStatus.FAIL
    assert result.next_action.code == PnrNextActionCode.REVIEW_PRICING


def test_different_but_present_contact_is_warning_not_blocking() -> None:
    result = _result(
        _snapshot(
            email="other@example.com",
            phone="+541199999999",
        )
    )

    assert _check(result, "CONTACT_PRESENT").status == PnrCheckStatus.PASS
    assert _check(result, "CONTACT_MATCH").status == PnrCheckStatus.WARN
    assert result.assessment.status == (
        PnrWorkspaceStatus.READY_FOR_TICKETING
    )
    assert result.next_action.code == PnrNextActionCode.ISSUE_TICKET


def test_known_passenger_type_mismatch_requires_passenger_review() -> None:
    result = _result(
        _snapshot(
            passenger_type="INF",
        )
    )

    assert _check(result, "PASSENGER_TYPES_MATCH").status == (
        PnrCheckStatus.FAIL
    )
    assert result.assessment.status == PnrWorkspaceStatus.NEEDS_ATTENTION
    assert result.next_action.code == PnrNextActionCode.REVIEW_PASSENGERS


def test_non_active_pq_is_excluded_from_pricing_comparison() -> None:
    snapshot = _snapshot()
    snapshot.price_quotes.append(
        PnrPriceQuote(
            record_number="2",
            status="HISTORICAL",
            validating_carrier="AA",
            passenger_type="ADT",
            passenger_quantity=1,
            total_amount=Decimal("999.99"),
            total_currency="USD",
        )
    )

    result = _result(snapshot)

    assert result.pricing_selection.status == PnrPricingSelectionStatus.SELECTED
    assert result.pricing_selection.candidate_record_numbers == ["1"]
    assert result.pricing_selection.total_quote_count == 2
    assert result.pricing_selection.candidate_quote_count == 1
    assert result.pricing_selection.excluded_quote_count == 1
    assert _check(result, "ACTIVE_PRICING_SELECTED").status == PnrCheckStatus.PASS
    assert _check(result, "PRICE_MATCH").status == PnrCheckStatus.PASS
    assert result.assessment.status == PnrWorkspaceStatus.READY_FOR_TICKETING


def test_only_non_active_pq_requires_pricing_review() -> None:
    snapshot = _snapshot()
    snapshot.price_quotes[0].status = "HISTORICAL"

    result = _result(snapshot)

    assert result.pricing_selection.status == PnrPricingSelectionStatus.NO_ACTIVE
    assert _check(result, "PRICING_PRESENT").status == PnrCheckStatus.PASS
    assert _check(result, "ACTIVE_PRICING_SELECTED").status == PnrCheckStatus.FAIL
    assert _check(result, "CURRENCY_MATCH").status == PnrCheckStatus.UNKNOWN
    assert _check(result, "PRICE_MATCH").status == PnrCheckStatus.UNKNOWN
    assert result.assessment.status == PnrWorkspaceStatus.NEEDS_ATTENTION
    assert result.next_action.code == PnrNextActionCode.REVIEW_PRICING

def test_active_pq_without_name_association_blocks_ticketing() -> None:
    snapshot = _snapshot()
    snapshot.price_quotes[0].passenger_name_numbers = []
    result = _result(snapshot)
    assert result.pricing_coverage.status == PnrPricingCoverageStatus.UNKNOWN
    assert _check(result, "PRICING_PASSENGER_COVERAGE").status == PnrCheckStatus.UNKNOWN
    assert result.assessment.status == PnrWorkspaceStatus.NEEDS_ATTENTION
    assert result.next_action.code == PnrNextActionCode.REVIEW_PRICING


def test_active_pq_wrong_passenger_association_blocks_ticketing() -> None:
    snapshot = _snapshot()
    snapshot.price_quotes[0].passenger_name_numbers = ["09.09"]
    result = _result(snapshot)
    assert result.pricing_coverage.status == PnrPricingCoverageStatus.CONFLICT
    assert _check(result, "PRICING_PASSENGER_COVERAGE").status == PnrCheckStatus.FAIL
    assert result.next_action.code == PnrNextActionCode.REVIEW_PRICING

