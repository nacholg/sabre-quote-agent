from xml.etree import ElementTree as ET

from app.sabre.pnr_snapshot_parser import parse_pnr_snapshot


def _parse(flag: str | None):
    attr = (
        f' ItineraryChanged="{flag}"'
        if flag is not None
        else ""
    )
    root = ET.fromstring(
        f"""\
<TravelItineraryReadRS>
  <TravelItinerary>
    <ItineraryInfo>
      <ItineraryPricing>
        <PriceQuote RPH="1">
          <MiscInformation>
            <SignatureLine Status="ACTIVE"/>
          </MiscInformation>
          <PricedItinerary RPH="1">
            <AirItineraryPricingInfo>
              <PassengerTypeQuantity Code="ADT" Quantity="01"/>
              <PTC_FareBreakdown/>
            </AirItineraryPricingInfo>
          </PricedItinerary>
          <PriceQuotePlus{attr}>
            <PassengerInfo PassengerType="ADT">
              <PassengerData NameNumber="01.01">TEST</PassengerData>
            </PassengerInfo>
          </PriceQuotePlus>
        </PriceQuote>
      </ItineraryPricing>
      <ReservationItems/>
    </ItineraryInfo>
  </TravelItinerary>
</TravelItineraryReadRS>
"""
    )
    return parse_pnr_snapshot(
        root,
        confirmation_id="OVFOTM",
        application_status="Complete",
    ).price_quotes[0]


def test_parser_reads_itinerary_changed_true() -> None:
    quote = _parse("true")
    assert quote.status == "ACTIVE"
    assert quote.itinerary_changed is True


def test_parser_reads_itinerary_changed_false() -> None:
    quote = _parse("false")
    assert quote.itinerary_changed is False


def test_parser_preserves_missing_itinerary_changed_as_unknown() -> None:
    quote = _parse(None)
    assert quote.itinerary_changed is None
