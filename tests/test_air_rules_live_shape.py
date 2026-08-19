from datetime import date

from app.sabre.air_rules import (
    AirRulesRequest,
    build_air_rules_request,
    parse_air_rules_response,
)


def test_build_air_rules_request_matches_validated_sabre_schema():
    xml = build_air_rules_request(
        AirRulesRequest(
            pcc="RY3A",
            conversation_id="conv-123",
            binary_security_token="TOKEN123",
            origin="EZE",
            destination="JFK",
            departure_date=date(2027, 1, 20),
            carrier="AA",
            fare_basis="LLX5ABM1",
            category=16,
        )
    )

    assert 'Version="2.3.0"' in xml
    assert '<FlightSegment DepartureDateTime="2027-01-20">' in xml
    assert 'DestinationLocation LocationCode="JFK"' in xml
    assert 'MarketingCarrier Code="AA"' in xml
    assert 'OriginLocation LocationCode="EZE"' in xml
    assert "<Category>16</Category>" in xml
    assert 'FareBasis Code="LLX5ABM1"' in xml

    # Regression: these were rejected by Sabre CERT's XSD.
    assert "2027-01-20T00:00:00" not in xml
    assert "MarketingAirline" not in xml
    assert "FlightNumber" not in xml

    destination_pos = xml.index("<DestinationLocation")
    carrier_pos = xml.index("<MarketingCarrier")
    origin_pos = xml.index("<OriginLocation")
    assert destination_pos < carrier_pos < origin_pos

    category_pos = xml.index("<Category>")
    fare_basis_pos = xml.index("<FareBasis")
    assert category_pos < fare_basis_pos


def test_parse_real_sabre_category_16_paragraph():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <soap-env:Envelope
      xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
      <soap-env:Body>
        <OTA_AirRulesRS
          xmlns="http://webservices.sabre.com/sabreXML/2011/10"
          Version="2.3.0">
          <FareRuleInfo>
            <Rules>
              <Paragraph RPH="16" Title="PENALTIES">
                <Text>CANCELLATIONS
ANY TIME
TICKET IS NON-REFUNDABLE IN CASE OF CANCEL/NO-SHOW/REFUND.
CHANGES
BEFORE DEPARTURE
CHANGES PERMITTED.
AFTER DEPARTURE
CHANGES PERMITTED.</Text>
              </Paragraph>
            </Rules>
          </FareRuleInfo>
        </OTA_AirRulesRS>
      </soap-env:Body>
    </soap-env:Envelope>"""

    parsed = parse_air_rules_response(xml)

    assert parsed.success is True
    assert len(parsed.categories) == 1

    category = parsed.categories[0]
    assert category.number == 16
    assert category.title == "PENALTIES"
    assert "NON-REFUNDABLE" in category.text
    assert "CHANGES PERMITTED" in category.text


def test_parse_air_rules_fault_with_validation_message():
    xml = """<Envelope><Body><Fault>
      <faultcode>soap-env:Client.Validation</faultcode>
      <faultstring>ERR.SWS.CLIENT.VALIDATION_FAILED</faultstring>
      <detail>
        <ApplicationResults>
          <Error>
            <SystemSpecificResults>
              <Message>Invalid request shape.</Message>
              <ShortText>ERR.SWS.CLIENT.VALIDATION_FAILED</ShortText>
            </SystemSpecificResults>
          </Error>
        </ApplicationResults>
      </detail>
    </Fault></Body></Envelope>"""

    parsed = parse_air_rules_response(xml)

    assert parsed.success is False
    assert parsed.fault_code == "soap-env:Client.Validation"
    assert "Invalid request shape." in parsed.application_errors
