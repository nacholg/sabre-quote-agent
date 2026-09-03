from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

from app.sabre.pnr_snapshot_parser import parse_pnr_snapshot


FULL_TIR_XML = """\
<soap-env:Envelope
    xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:stl="http://services.sabre.com/STL/v01"
    xmlns:tir="http://services.sabre.com/res/tir/v3_10">
  <soap-env:Body>
    <tir:TravelItineraryReadRS Version="3.10.0">
      <stl:ApplicationResults status="Complete"/>
      <tir:TravelItinerary>
        <tir:AgencyInfo>
          <tir:Ticketing TicketType="7TAW"/>
        </tir:AgencyInfo>
        <tir:CustomerInfo>
          <tir:ContactNumbers>
            <tir:ContactNumber
                LocationCode="BUE"
                Phone="541155551234-A"
                RPH="001"/>
          </tir:ContactNumbers>
          <tir:PersonName
              NameNumber="01.01"
              PassengerType="ADT"
              RPH="1"
              WithInfant="false">
            <tir:Email Comment="TO/" Type="TO">PAX@EXAMPLE.COM</tir:Email>
            <tir:GivenName>JUAN MR</tir:GivenName>
            <tir:Surname>LOPEZ</tir:Surname>
          </tir:PersonName>
        </tir:CustomerInfo>
        <tir:ItineraryInfo>
          <tir:ItineraryPricing>
            <tir:PriceQuote RPH="1">
              <tir:MiscInformation>
                <tir:SignatureLine Status="ACTIVE"/>
              </tir:MiscInformation>
              <tir:PricedItinerary
                  RPH="1"
                  StoredDateTime="2026-09-01T10:00"
                  ValidatingCarrier="AA">
                <tir:AirItineraryPricingInfo>
                  <tir:ItinTotalFare>
                    <tir:BaseFare Amount="1000.00" CurrencyCode="USD"/>
                    <tir:Taxes>
                      <tir:Tax Amount="258.93" TaxCode="XT"/>
                    </tir:Taxes>
                    <tir:TotalFare Amount="1258.93" CurrencyCode="USD"/>
                    <tir:Totals>
                      <tir:TotalFare Amount="1258.93"/>
                    </tir:Totals>
                  </tir:ItinTotalFare>
                  <tir:PassengerTypeQuantity Code="ADT" Quantity="01"/>
                  <tir:PTC_FareBreakdown>
                    <tir:FareBasis Code="NLN0DTM5/L040"/>
                    <tir:FlightSegment
                        FlightNumber="908"
                        ResBookDesigCode="N"
                        SegmentNumber="1"
                        Status="OK">
                      <tir:MarketingAirline Code="AA" FlightNumber="908"/>
                      <tir:OriginLocation LocationCode="EZE"/>
                    </tir:FlightSegment>
                  </tir:PTC_FareBreakdown>
                </tir:AirItineraryPricingInfo>
              </tir:PricedItinerary>
              <tir:PriceQuotePlus>
                <tir:PassengerInfo PassengerType="ADT">
                  <tir:PassengerData NameNumber="01.01">LOPEZ/JUAN MR</tir:PassengerData>
                </tir:PassengerInfo>
              </tir:PriceQuotePlus>
            </tir:PriceQuote>
          </tir:ItineraryPricing>
          <tir:ReservationItems>
            <tir:Item RPH="1">
              <tir:FlightSegment
                  ArrivalDateTime="09-20T06:45"
                  DepartureDateTime="2026-09-19T22:20:00"
                  FlightNumber="0908"
                  NumberInParty="01"
                  ResBookDesigCode="N"
                  SegmentNumber="1"
                  Status="HK"
                  eTicket="true">
                <tir:DestinationLocation LocationCode="MIA"/>
                <tir:MarketingAirline Code="AA" FlightNumber="0908"/>
                <tir:OperatingAirline Code="AA"/>
                <tir:OriginLocation LocationCode="EZE"/>
                <tir:SupplierRef ID="DCAA*ABC123"/>
              </tir:FlightSegment>
            </tir:Item>
          </tir:ReservationItems>
        </tir:ItineraryInfo>
        <tir:OpenReservationElements>
          <tir:OpenReservationElement type="SRVC">
            <tir:ServiceRequest
                actionCode="HK"
                airlineCode="AA"
                code="DOCS"
                serviceType="SSR">
              <tir:FreeText>PII MUST NOT BE COPIED INTO SNAPSHOT</tir:FreeText>
            </tir:ServiceRequest>
            <tir:NameAssociation>
              <tir:NameRefNumber>01.01</tir:NameRefNumber>
            </tir:NameAssociation>
            <tir:SegmentAssociation>
              <tir:SegmentRefNumber>1</tir:SegmentRefNumber>
            </tir:SegmentAssociation>
          </tir:OpenReservationElement>
          <tir:OpenReservationElement type="SRVC">
            <tir:ServiceRequest
                actionCode="KK"
                airlineCode="1S"
                code="ADTK"
                serviceType="SSR">
              <tir:FreeText>UNSTRUCTURED TICKETING ADVICE</tir:FreeText>
            </tir:ServiceRequest>
          </tir:OpenReservationElement>
        </tir:OpenReservationElements>
      </tir:TravelItinerary>
    </tir:TravelItineraryReadRS>
  </soap-env:Body>
</soap-env:Envelope>
"""


def test_parser_normalizes_customer_itinerary_pricing_and_ssr() -> None:
    snapshot = parse_pnr_snapshot(
        ET.fromstring(FULL_TIR_XML),
        confirmation_id="ovmpdh",
        application_status="Complete",
    )

    assert snapshot.confirmation_id == "OVMPDH"
    assert snapshot.application_status == "Complete"

    assert len(snapshot.passengers) == 1
    passenger = snapshot.passengers[0]
    assert passenger.name_number == "01.01"
    assert passenger.passenger_type == "ADT"
    assert passenger.given_name == "JUAN MR"
    assert passenger.surname == "LOPEZ"
    assert passenger.with_infant is False
    assert passenger.emails == ["PAX@EXAMPLE.COM"]

    assert {(item.kind, item.value) for item in snapshot.contacts} == {
        ("phone", "541155551234-A"),
        ("email", "PAX@EXAMPLE.COM"),
    }

    assert len(snapshot.segments) == 1
    segment = snapshot.segments[0]
    assert segment.marketing_carrier == "AA"
    assert segment.operating_carrier == "AA"
    assert segment.flight_number == "908"
    assert segment.origin == "EZE"
    assert segment.destination == "MIA"
    assert segment.departure_at == "2026-09-19T22:20:00"
    assert segment.arrival_at == "2026-09-20T06:45"
    assert segment.booking_class == "N"
    assert segment.status == "HK"
    assert segment.number_in_party == 1
    assert segment.airline_locator == "DCAA*ABC123"
    assert segment.e_ticket is True

    assert len(snapshot.price_quotes) == 1
    pq = snapshot.price_quotes[0]
    assert pq.record_number == "1"
    assert pq.status == "ACTIVE"
    assert pq.validating_carrier == "AA"
    assert pq.passenger_type == "ADT"
    assert pq.passenger_quantity == 1
    assert pq.passenger_name_numbers == ["01.01"]
    assert pq.base_fare_amount == Decimal("1000.00")
    assert pq.per_passenger_tax_amount == Decimal("258.93")
    assert pq.per_passenger_total_amount == Decimal("1258.93")
    assert pq.total_amount == Decimal("1258.93")
    assert pq.total_currency == "USD"
    assert pq.fare_basis == "NLN0DTM5/L040"
    assert pq.fare_basis_codes == ["NLN0DTM5/L040"]
    assert pq.segment_booking_classes == ["N"]

    assert snapshot.ticketing.ticket_type == "7TAW"
    assert snapshot.ticketing.advisory_present is True
    assert snapshot.ticketing.advisory_code == "ADTK"
    assert snapshot.ticketing.advisory_status == "KK"
    assert snapshot.ticketing.advisory_airline_code == "1S"
    assert snapshot.ticketing.deadline_at is None
    assert len(snapshot.special_services) == 2
    ssr = snapshot.special_services[0]
    assert ssr.code == "DOCS"
    assert ssr.status == "HK"
    assert ssr.airline_code == "AA"
    assert ssr.name_numbers == ["01.01"]
    assert ssr.segment_numbers == ["1"]

    # The normalized v0.34 snapshot deliberately does not retain SSR free text.
    dumped = snapshot.model_dump_json()
    assert "PII MUST NOT" not in dumped
    assert "UNSTRUCTURED TICKETING ADVICE" not in dumped


def test_parser_counts_only_reservation_segments_not_pq_segments() -> None:
    snapshot = parse_pnr_snapshot(
        ET.fromstring(FULL_TIR_XML),
        confirmation_id="OVMPDH",
        application_status="Complete",
    )

    # The fixture contains one ReservationItems FlightSegment and one pricing
    # FlightSegment inside the PQ. Only the actual booked segment belongs here.
    assert len(snapshot.segments) == 1


def test_parser_tolerates_minimal_read_response() -> None:
    root = ET.fromstring(
        """\
<TravelItineraryReadRS>
  <TravelItinerary>
    <ItineraryInfo>
      <ReservationItems>
        <Item><FlightSegment FlightNumber="900"/></Item>
      </ReservationItems>
    </ItineraryInfo>
  </TravelItinerary>
</TravelItineraryReadRS>
"""
    )

    snapshot = parse_pnr_snapshot(
        root,
        confirmation_id="ABCDEF",
        application_status="Complete",
    )

    assert snapshot.confirmation_id == "ABCDEF"
    assert len(snapshot.segments) == 1
    assert snapshot.segments[0].flight_number == "900"
    assert snapshot.passengers == []
    assert snapshot.price_quotes == []


def test_parser_expands_partial_arrival_across_year_boundary() -> None:
    root = ET.fromstring(
        """\
<TravelItineraryReadRS>
  <TravelItinerary>
    <ItineraryInfo>
      <ReservationItems>
        <Item>
          <FlightSegment
              DepartureDateTime="2026-12-31T23:50"
              ArrivalDateTime="01-01T05:10"
              FlightNumber="0900">
            <MarketingAirline Code="AA"/>
          </FlightSegment>
        </Item>
      </ReservationItems>
    </ItineraryInfo>
  </TravelItinerary>
</TravelItineraryReadRS>
"""
    )

    snapshot = parse_pnr_snapshot(
        root,
        confirmation_id="ABCDEF",
        application_status="Complete",
    )

    assert snapshot.segments[0].flight_number == "900"
    assert snapshot.segments[0].arrival_at == "2027-01-01T05:10"
