from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

from app.models.pnr_workspace import (
    PnrPreIssueReadiness,
    PnrPreIssueReadinessStatus,
    PnrPriceQuote,
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrPurchaseDeadlineStatus,
    PnrTicketingConstraint,
    PnrTicketingConstraintStatus,
)
from app.sabre.pnr_snapshot_parser import parse_pnr_snapshot
from app.services.pnr_final_pre_issue_gate_service import (
    build_pnr_final_pre_issue_gate,
)
from app.services.pnr_purchase_deadline_service import (
    build_pnr_purchase_deadline,
)


TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _selection(*quotes: PnrPriceQuote) -> PnrPricingSelection:
    return PnrPricingSelection(
        status=PnrPricingSelectionStatus.SELECTED,
        candidates=list(quotes),
        total_quote_count=len(quotes),
        candidate_quote_count=len(quotes),
        candidate_record_numbers=[
            q.record_number for q in quotes if q.record_number
        ],
    )


def _quote(
    *,
    raw: str | None,
    stored_at: str = "2026-09-01T10:00:00",
    record: str = "1",
) -> PnrPriceQuote:
    return PnrPriceQuote(
        record_number=record,
        status="ACTIVE",
        stored_at=stored_at,
        validating_carrier="AA",
        passenger_type="ADT",
        passenger_quantity=1,
        passenger_name_numbers=["01.01"],
        total_amount=Decimal("781.33"),
        total_currency="USD",
        purchase_deadline_raw=raw,
    )


def _ready_pre_issue() -> PnrPreIssueReadiness:
    return PnrPreIssueReadiness(
        status=PnrPreIssueReadinessStatus.READY,
        confirmation_id="OVFOTM",
        retrieved_at="2026-09-04T13:26:53+00:00",
        fresh_remote_read=True,
    )


def test_parser_extracts_real_cert_last_day_to_purchase_only() -> None:
    root = ET.fromstring(
        """\
<TravelItineraryReadRS>
  <TravelItinerary>
    <ItineraryInfo>
      <ItineraryPricing>
        <PriceQuote RPH="1">
          <MiscInformation>
            <SignatureLine Status="ACTIVE"/>
          </MiscInformation>
          <PricedItinerary RPH="1" StoredDateTime="2026-09-01T10:00:00">
            <AirItineraryPricingInfo>
              <PassengerTypeQuantity Code="ADT" Quantity="01"/>
              <PTC_FareBreakdown>
                <ResTicketingRestrictions>
                  LAST DAY TO PURCHASE 03SEP/2359
                </ResTicketingRestrictions>
                <ResTicketingRestrictions>
                  GUARANTEED FARE APPL IF PURCHASED BEFORE 03SEP
                </ResTicketingRestrictions>
              </PTC_FareBreakdown>
            </AirItineraryPricingInfo>
          </PricedItinerary>
        </PriceQuote>
      </ItineraryPricing>
      <ReservationItems/>
    </ItineraryInfo>
  </TravelItinerary>
</TravelItineraryReadRS>
"""
    )

    snapshot = parse_pnr_snapshot(
        root,
        confirmation_id="OVFOTM",
        application_status="Complete",
    )

    assert snapshot.price_quotes[0].purchase_deadline_raw == (
        "LAST DAY TO PURCHASE 03SEP/2359"
    )
    dumped = snapshot.model_dump_json()
    assert "GUARANTEED FARE APPL" not in dumped


def test_real_ovfotm_deadline_is_expired_on_sep4() -> None:
    result = build_pnr_purchase_deadline(
        _selection(
            _quote(raw="LAST DAY TO PURCHASE 03SEP/2359")
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )

    assert result.status == PnrPurchaseDeadlineStatus.EXPIRED
    assert result.purchase_deadline_at == "2026-09-03T23:59:00-03:00"
    assert result.blockers == ["PURCHASE_DEADLINE_EXPIRED"]


def test_deadline_within_24_hours_is_used_directly() -> None:
    result = build_pnr_purchase_deadline(
        _selection(
            _quote(raw="LAST DAY TO PURCHASE 05SEP/0900")
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )

    assert result.status == PnrPurchaseDeadlineStatus.RESOLVED
    assert result.purchase_deadline_at == "2026-09-05T09:00:00-03:00"
    assert result.operational_deadline_at == result.purchase_deadline_at
    assert result.policy_capped is False


def test_more_than_24_hours_caps_at_tomorrow_noon() -> None:
    result = build_pnr_purchase_deadline(
        _selection(
            _quote(raw="LAST DAY TO PURCHASE 06SEP/2359")
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )

    assert result.status == PnrPurchaseDeadlineStatus.RESOLVED
    assert result.purchase_deadline_at == "2026-09-06T23:59:00-03:00"
    assert result.operational_deadline_at == "2026-09-05T12:00:00-03:00"
    assert result.policy_cap_at == "2026-09-05T12:00:00-03:00"
    assert result.policy_capped is True


def test_policy_never_extends_beyond_purchase_deadline() -> None:
    result = build_pnr_purchase_deadline(
        _selection(
            _quote(raw="LAST DAY TO PURCHASE 05SEP/1100")
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )

    assert result.purchase_deadline_at == "2026-09-05T11:00:00-03:00"
    assert result.operational_deadline_at == "2026-09-05T11:00:00-03:00"
    assert result.policy_capped is False


def test_date_only_deadline_is_fail_closed() -> None:
    result = build_pnr_purchase_deadline(
        _selection(
            _quote(raw="LAST DAY TO PURCHASE 05SEP")
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )

    assert result.status == PnrPurchaseDeadlineStatus.UNRESOLVED
    assert result.blockers == ["PURCHASE_DEADLINE_TIME_MISSING"]


def test_multiple_active_pqs_use_earliest_resolved_deadline() -> None:
    result = build_pnr_purchase_deadline(
        _selection(
            _quote(
                raw="LAST DAY TO PURCHASE 06SEP/1800",
                record="1",
            ),
            _quote(
                raw="LAST DAY TO PURCHASE 05SEP/2000",
                record="2",
            ),
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )

    assert result.purchase_deadline_at == "2026-09-05T20:00:00-03:00"
    assert result.operational_deadline_at == "2026-09-05T12:00:00-03:00"
    assert result.source_record_numbers == ["1", "2"]


def test_one_missing_deadline_among_active_pqs_blocks() -> None:
    result = build_pnr_purchase_deadline(
        _selection(
            _quote(
                raw="LAST DAY TO PURCHASE 06SEP/1800",
                record="1",
            ),
            _quote(raw=None, record="2"),
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )

    assert result.status == PnrPurchaseDeadlineStatus.UNRESOLVED
    assert result.blockers == ["PURCHASE_DEADLINE_MISSING"]


def test_year_rollover_uses_pq_stored_date_as_anchor() -> None:
    result = build_pnr_purchase_deadline(
        _selection(
            _quote(
                raw="LAST DAY TO PURCHASE 02JAN/1200",
                stored_at="2026-12-31T10:00:00",
            )
        ),
        now=datetime(2026, 12, 31, 10, 0, tzinfo=TZ),
    )

    assert result.purchase_deadline_at == "2027-01-02T12:00:00-03:00"


def test_resolved_purchase_deadline_supersedes_unresolved_adtk_gate() -> None:
    purchase = build_pnr_purchase_deadline(
        _selection(
            _quote(raw="LAST DAY TO PURCHASE 05SEP/0900")
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )
    constraint = PnrTicketingConstraint(
        status=PnrTicketingConstraintStatus.ADVISORY_WITHOUT_DEADLINE,
        advisory_present=True,
        advisory_code="ADTK",
        requires_deadline_lookup=True,
    )

    gate = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=constraint,
        purchase_deadline=purchase,
        now=datetime(2026, 9, 4, 13, 0, tzinfo=ZoneInfo("UTC")),
    )

    assert gate.status.value == "ready"
    assert gate.blockers == []
    assert gate.purchase_deadline_status.value == "resolved"


def test_expired_purchase_deadline_blocks_with_precise_reason() -> None:
    purchase = build_pnr_purchase_deadline(
        _selection(
            _quote(raw="LAST DAY TO PURCHASE 03SEP/2359")
        ),
        now=datetime(2026, 9, 4, 10, 0, tzinfo=TZ),
    )
    constraint = PnrTicketingConstraint(
        status=PnrTicketingConstraintStatus.ADVISORY_WITHOUT_DEADLINE,
        advisory_present=True,
        advisory_code="ADTK",
        requires_deadline_lookup=True,
    )

    gate = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=_ready_pre_issue(),
        ticketing_constraint=constraint,
        purchase_deadline=purchase,
        now=datetime(2026, 9, 4, 13, 0, tzinfo=ZoneInfo("UTC")),
    )

    assert gate.status.value == "blocked"
    assert gate.blockers == ["PURCHASE_DEADLINE_EXPIRED"]
    assert gate.deadline_expired is True
