from datetime import date, time

from app.models.api import AgentQuoteRequest
from app.models.quote_request import DayPart, TimeConstraintMode, TimeEvent
from app.services.agent_parser import parse_agent_quote

TODAY = date(2026, 8, 18)


def test_arrival_led_round_trip_with_dayparts():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "Cotizar vuelos que lleguen a Miami desde Buenos Aires, "
                "el 11 de febrero a la mañana, con regreso a Buenos Aires "
                "el 20 de febrero por la noche, llegando el 21 de febrero."
            ),
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.origin == "EZE"
    assert req.destination == "MIA"
    assert str(req.departure_date) == "2027-02-10"
    assert str(req.return_date) == "2027-02-20"
    assert len(req.time_constraints) == 3

    out = req.time_constraints[0]
    assert out.leg_index == 0
    assert out.event == TimeEvent.ARRIVAL
    assert str(out.date) == "2027-02-11"
    assert out.daypart == DayPart.MORNING
    assert out.time_from == time(6, 0)
    assert out.time_to == time(11, 59)
    assert out.mode == TimeConstraintMode.REQUIRED

    ret = req.time_constraints[1]
    assert ret.leg_index == 1
    assert ret.event == TimeEvent.DEPARTURE
    assert str(ret.date) == "2027-02-20"
    assert ret.daypart == DayPart.NIGHT
    assert ret.time_from == time(19, 0)
    assert ret.time_to == time(2, 59)
    assert ret.wraps_midnight is True

    arr = req.time_constraints[2]
    assert arr.leg_index == 1
    assert arr.event == TimeEvent.ARRIVAL
    assert str(arr.date) == "2027-02-21"


def test_preferentemente_marks_time_constraint_as_preferred():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizar EZE MIA, llegando el 11 de febrero preferentemente a la mañana.",
            execute=False,
        ),
        today=TODAY,
    )
    c = parsed.search_request.time_constraints[0]
    assert c.event == TimeEvent.ARRIVAL
    assert c.mode == TimeConstraintMode.PREFERRED


def test_night_window_crosses_midnight():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizar EZE MIA saliendo el 10 de febrero por la noche.",
            execute=False,
        ),
        today=TODAY,
    )
    c = parsed.search_request.time_constraints[0]
    assert c.event == TimeEvent.DEPARTURE
    assert c.daypart == DayPart.NIGHT
    assert c.wraps_midnight is True
    assert c.time_from == time(19, 0)
    assert c.time_to == time(2, 59)


def test_repeated_route_locations_do_not_trigger_open_jaw_warning():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "Cotizar vuelos que lleguen a Miami desde Buenos Aires "
                "el 11 de febrero a la mañana, con regreso a Buenos Aires "
                "el 20 de febrero por la noche, llegando el 21 de febrero."
            ),
            execute=False,
        ),
        today=TODAY,
    )

    assert parsed.search_request.origin == "EZE"
    assert parsed.search_request.destination == "MIA"
    assert not any(
        "más de dos aeropuertos" in warning
        for warning in parsed.warnings
    )


def test_departure_after_explicit_hour():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizar EZE MIA saliendo el 10 de febrero después de las 20.",
            execute=False,
        ),
        today=TODAY,
    )
    c = parsed.search_request.time_constraints[0]
    assert c.event == TimeEvent.DEPARTURE
    assert c.time_from == time(20, 0)
    assert c.time_to is None
    assert c.mode == TimeConstraintMode.REQUIRED


def test_arrival_before_explicit_hour():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizar EZE MIA llegando el 11 de febrero antes de las 10.",
            execute=False,
        ),
        today=TODAY,
    )
    c = parsed.search_request.time_constraints[0]
    assert c.event == TimeEvent.ARRIVAL
    assert c.time_from is None
    assert c.time_to == time(10, 0)


def test_between_explicit_hours():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizar EZE MIA saliendo el 10 de febrero entre las 18 y 22 hs.",
            execute=False,
        ),
        today=TODAY,
    )
    c = parsed.search_request.time_constraints[0]
    assert c.time_from == time(18, 0)
    assert c.time_to == time(22, 0)
    assert c.wraps_midnight is False


def test_no_antes_de_maps_to_lower_bound():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizar EZE MIA saliendo el 10 de febrero no antes de las 19.",
            execute=False,
        ),
        today=TODAY,
    )
    c = parsed.search_request.time_constraints[0]
    assert c.time_from == time(19, 0)
    assert c.time_to is None


def test_como_maximo_maps_to_upper_bound():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizar EZE MIA llegando el 11 de febrero como máximo a las 14.",
            execute=False,
        ),
        today=TODAY,
    )
    c = parsed.search_request.time_constraints[0]
    assert c.time_from is None
    assert c.time_to == time(14, 0)


def test_around_time_is_preferred_one_hour_window():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "Cotizar EZE MIA llegando el 11 de febrero "
                "alrededor de las 8 de la mañana."
            ),
            execute=False,
        ),
        today=TODAY,
    )
    c = parsed.search_request.time_constraints[0]
    assert c.event == TimeEvent.ARRIVAL
    assert c.time_from == time(7, 0)
    assert c.time_to == time(9, 0)
    assert c.mode == TimeConstraintMode.PREFERRED


def test_return_explicit_time_constraints():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "Cotizar EZE MIA saliendo el 10 de febrero después de las 20, "
                "con regreso el 20 de febrero entre las 18 y 22."
            ),
            execute=False,
        ),
        today=TODAY,
    )

    assert len(parsed.search_request.time_constraints) == 2
    out, ret = parsed.search_request.time_constraints
    assert out.leg_index == 0
    assert out.time_from == time(20, 0)
    assert ret.leg_index == 1
    assert ret.time_from == time(18, 0)
    assert ret.time_to == time(22, 0)
