from xml.etree import ElementTree as ET

from app.sabre.pnr_snapshot_parser import parse_pnr_snapshot
from app.services.pnr_final_pre_issue_gate_service import (
    build_pnr_final_pre_issue_gate,
)
from app.services.pnr_pre_issue_readiness_service import (
    build_pnr_pre_issue_readiness,
)
from app.services.pnr_ticketing_constraint_service import (
    interpret_pnr_ticketing_constraint,
)
from app.models.pnr_workspace import (
    PnrAssessment,
    PnrPreIssueReadinessStatus,
    PnrTicketCandidate,
    PnrTicketCandidateStatus,
    PnrWorkspaceStatus,
)


def _parse(ticketing_xml: str, *, agency_ticketing: str = ""):
    root = ET.fromstring(
        f"""\
<TravelItineraryReadRS>
  <TravelItinerary>
    <AgencyInfo>{agency_ticketing}</AgencyInfo>
    <ItineraryInfo>
      {ticketing_xml}
      <ReservationItems/>
    </ItineraryInfo>
    <OpenReservationElements>
      <OpenReservationElement type="SRVC">
        <ServiceRequest
            actionCode="KK"
            airlineCode="1S"
            code="ADTK"
            serviceType="SSR">
          <FreeText>DO NOT PARSE THIS AS A DEADLINE</FreeText>
        </ServiceRequest>
      </OpenReservationElement>
    </OpenReservationElements>
  </TravelItinerary>
</TravelItineraryReadRS>
"""
    )
    return parse_pnr_snapshot(
        root,
        confirmation_id="OVFOTM",
        application_status="Complete",
    )


def test_real_cert_ticketing_node_is_read_from_itinerary_info() -> None:
    snapshot = _parse(
        '<Ticketing RPH="01" TicketTimeLimit="TAW/"/>'
    )

    ticketing = snapshot.ticketing
    assert ticketing.arrangement_raw == "TAW/"
    assert ticketing.arrangement_type == "TAW"
    assert ticketing.arrangement_rph == "01"
    assert ticketing.deadline_at is None
    assert ticketing.advisory_present is True
    assert ticketing.advisory_code == "ADTK"


def test_itinerary_info_ticketing_is_preferred_with_agency_fallback_fields() -> None:
    snapshot = _parse(
        '<Ticketing RPH="02" TicketTimeLimit="TAW23SEP/6P/"/>',
        agency_ticketing='<Ticketing TicketType="7TAW"/>',
    )

    ticketing = snapshot.ticketing
    assert ticketing.arrangement_raw == "TAW23SEP/6P/"
    assert ticketing.arrangement_type == "TAW"
    assert ticketing.arrangement_rph == "02"
    assert ticketing.ticket_type == "7TAW"
    assert ticketing.deadline_at is None


def test_agency_info_ticketing_remains_backward_compatible() -> None:
    snapshot = _parse(
        "",
        agency_ticketing=(
            '<Ticketing RPH="03" TicketType="7TAW" '
            'TicketTimeLimit="TAW/"/>'
        ),
    )

    ticketing = snapshot.ticketing
    assert ticketing.ticket_type == "7TAW"
    assert ticketing.arrangement_raw == "TAW/"
    assert ticketing.arrangement_type == "TAW"
    assert ticketing.arrangement_rph == "03"


def test_ticket_time_limit_raw_is_never_promoted_to_deadline() -> None:
    snapshot = _parse(
        '<Ticketing RPH="01" TicketTimeLimit="05-23T23:00"/>'
    )

    assert snapshot.ticketing.arrangement_raw == "05-23T23:00"
    assert snapshot.ticketing.arrangement_type is None
    assert snapshot.ticketing.deadline_at is None


def test_taw_plus_adtk_remains_unresolved_for_final_gate() -> None:
    snapshot = _parse(
        '<Ticketing RPH="01" TicketTimeLimit="TAW/"/>'
    )
    constraint = interpret_pnr_ticketing_constraint(
        snapshot.ticketing
    )

    assert constraint.status.value == "advisory_without_deadline"
    assert constraint.requires_deadline_lookup is True
    assert constraint.deadline_at is None

    assessment = PnrAssessment(
        status=PnrWorkspaceStatus.READY_FOR_TICKETING
    )
    candidate = PnrTicketCandidate(
        status=PnrTicketCandidateStatus.READY,
        confirmation_id="OVFOTM",
    )
    readiness = build_pnr_pre_issue_readiness(
        confirmation_id="OVFOTM",
        retrieved_at="2026-09-03T20:48:28+00:00",
        stale=False,
        workspace_status=PnrWorkspaceStatus.READY_FOR_TICKETING,
        read_error_code=None,
        assessment=assessment,
        ticket_candidate=candidate,
    )
    assert readiness.status == PnrPreIssueReadinessStatus.READY

    final_gate = build_pnr_final_pre_issue_gate(
        confirmation_id="OVFOTM",
        pre_issue_readiness=readiness,
        ticketing_constraint=constraint,
    )
    assert final_gate.status.value == "blocked"
    assert final_gate.blockers == ["TICKETING_DEADLINE_UNRESOLVED"]


def test_ssr_free_text_is_still_not_retained() -> None:
    snapshot = _parse(
        '<Ticketing RPH="01" TicketTimeLimit="TAW/"/>'
    )

    dumped = snapshot.model_dump_json()
    assert "DO NOT PARSE THIS AS A DEADLINE" not in dumped
