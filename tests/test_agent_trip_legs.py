from datetime import date

from app.models.api import AgentQuoteRequest
from app.models.quote_request import TripType
from app.services.agent_parser import parse_agent_quote


TODAY = date(2026, 8, 19)


def parse(text):
    return parse_agent_quote(
        AgentQuoteRequest(text=text, execute=False),
        today=TODAY,
    )


def test_one_way_compact_leg():
    parsed = parse("EZE-MIA 10DEC, 1 adulto, USD")
    req = parsed.search_request
    assert req.trip_type == TripType.ONE_WAY
    assert [(x.origin, x.destination, str(x.departure_date)) for x in req.legs] == [
        ("EZE", "MIA", "2026-12-10"),
    ]


def test_round_trip_legacy_text_has_canonical_legs():
    parsed = parse(
        "EZE-MIA del 10 al 30 de diciembre, 1 adulto, USD"
    )
    req = parsed.search_request
    assert req.trip_type == TripType.ROUND_TRIP
    assert [(x.origin, x.destination) for x in req.legs] == [
        ("EZE", "MIA"),
        ("MIA", "EZE"),
    ]


def test_open_jaw_regreso_desde():
    parsed = parse(
        "EZE-MIA 10DEC regreso desde JFK 30DEC, 1 adulto, USD"
    )
    req = parsed.search_request
    assert req.trip_type == TripType.OPEN_JAW
    assert [(x.origin, x.destination, str(x.departure_date)) for x in req.legs] == [
        ("EZE", "MIA", "2026-12-10"),
        ("JFK", "EZE", "2026-12-30"),
    ]


def test_multi_city_compact_routes():
    parsed = parse(
        "EZE-MIA 10DEC, MIA-NYC 25DEC, JFK-EZE 30DEC, 1 adulto, USD"
    )
    req = parsed.search_request
    assert req.trip_type == TripType.MULTI_CITY
    assert [(x.origin, x.destination, str(x.departure_date)) for x in req.legs] == [
        ("EZE", "MIA", "2026-12-10"),
        ("MIA", "NYC", "2026-12-25"),
        ("JFK", "EZE", "2026-12-30"),
    ]
    assert not any(
        "más de dos aeropuertos" in warning.lower()
        for warning in parsed.warnings
    )


def test_city_codes_are_preserved():
    parsed = parse(
        "BUE-LON 10DEC regreso 30DEC, 1 adulto, USD"
    )
    req = parsed.search_request
    assert [(x.origin, x.destination) for x in req.legs] == [
        ("BUE", "LON"),
        ("LON", "BUE"),
    ]


def test_new_york_alias_resolves_to_city_code():
    parsed = parse(
        "MIA Nueva York del 10 al 20 de diciembre, 1 adulto, USD"
    )
    assert parsed.search_request.destination == "NYC"
