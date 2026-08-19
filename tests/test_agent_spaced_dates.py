from datetime import date

from app.models.api import AgentQuoteRequest
from app.models.quote_request import TripType
from app.services.agent_parser import parse_agent_quote


TODAY = date(2026, 8, 19)


def _parse(text: str):
    return parse_agent_quote(
        AgentQuoteRequest(
            text=text,
            execute=False,
        ),
        today=TODAY,
    )


def test_agent_notation_round_trip_with_mixed_named_and_abbreviated_dates():
    parsed = _parse(
        "cotizar eze mia eze 20 de noviembre regreso 27 nov, "
        "1 adulto, USD, directo, economy"
    )
    req = parsed.search_request

    assert req.trip_type == TripType.ROUND_TRIP
    assert [
        (leg.origin, leg.destination, str(leg.departure_date))
        for leg in req.legs
    ] == [
        ("EZE", "MIA", "2026-11-20"),
        ("MIA", "EZE", "2026-11-27"),
    ]
    assert req.direct is True


def test_agent_notation_round_trip_with_spaced_abbreviated_dates():
    parsed = _parse(
        "EZE MIA EZE 20 nov regreso 27 nov, "
        "1 adulto, USD, economy"
    )
    req = parsed.search_request

    assert req.trip_type == TripType.ROUND_TRIP
    assert [
        (leg.origin, leg.destination, str(leg.departure_date))
        for leg in req.legs
    ] == [
        ("EZE", "MIA", "2026-11-20"),
        ("MIA", "EZE", "2026-11-27"),
    ]


def test_hyphen_route_mixed_date_styles_keeps_outbound_date():
    parsed = _parse(
        "EZE-MIA 20 de noviembre regreso 27 nov, "
        "1 adulto, USD"
    )
    req = parsed.search_request

    assert req.trip_type == TripType.ROUND_TRIP
    assert str(req.legs[0].departure_date) == "2026-11-20"
    assert str(req.legs[1].departure_date) == "2026-11-27"


def test_compact_date_without_space_still_works():
    parsed = _parse(
        "EZE-MIA 20NOV regreso 27NOV, 1 adulto, USD"
    )
    req = parsed.search_request

    assert req.trip_type == TripType.ROUND_TRIP
    assert str(req.legs[0].departure_date) == "2026-11-20"
    assert str(req.legs[1].departure_date) == "2026-11-27"
