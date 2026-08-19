from datetime import date

from app.sabre.air_rules import AirRulesRequest, build_air_rules_request, parse_air_rules_response


def test_build_air_rules_request_contains_contract_fields():
    xml = build_air_rules_request(AirRulesRequest(
        pcc="RY3A",
        conversation_id="conv-123",
        binary_security_token="TOKEN123",
        origin="EZE",
        destination="MIA",
        departure_date=date(2027, 2, 10),
        carrier="AA",
        fare_basis="OLX0N1M1",
        category=16,
    ))
    assert "OTA_AirRulesLLSRQ" in xml
    assert 'Version="2.3.0"' in xml
    assert 'LocationCode="EZE"' in xml
    assert 'LocationCode="MIA"' in xml
    assert 'Code="AA"' in xml
    assert 'FareBasis Code="OLX0N1M1"' in xml
    assert "<Category>16</Category>" in xml
    assert "TOKEN123" in xml


def test_parse_air_rules_fault():
    xml = """<Envelope><Body><Fault>
    <faultcode>soap-env:Client.AuthenticationFailed</faultcode>
    <faultstring>Authentication failed</faultstring>
    </Fault></Body></Envelope>"""
    parsed = parse_air_rules_response(xml)
    assert parsed.success is False
    assert parsed.fault_code == "soap-env:Client.AuthenticationFailed"
    assert parsed.fault_string == "Authentication failed"


def test_parse_air_rules_category_16_text():
    xml = """<Envelope><Body><OTA_AirRulesRS>
    <Rule Category="16" Title="PENALTIES">
      <Text>CHANGES PERMITTED WITH FEE USD 200.</Text>
      <Text>REFUND BEFORE DEPARTURE USD 300.</Text>
    </Rule>
    </OTA_AirRulesRS></Body></Envelope>"""
    parsed = parse_air_rules_response(xml)
    assert parsed.success is True
    assert len(parsed.categories) == 1
    category = parsed.categories[0]
    assert category.number == 16
    assert category.title == "PENALTIES"
    assert "USD 200" in category.text
    assert "USD 300" in category.text
