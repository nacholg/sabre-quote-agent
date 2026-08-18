from datetime import date

from app.services.time_parser import parse_time_constraints


TODAY = date(2026, 8, 18)


def test_plain_departure_date_does_not_create_time_constraint():
    constraints, inferred_departure, inferred_return, assumptions = (
        parse_time_constraints(
            "cotizar eze jfk con salida el 15 de diciembre "
            "y regreso el 22 de diciembre",
            today=TODAY,
        )
    )

    assert constraints == []
    assert inferred_departure is None
    assert inferred_return is None


def test_arrival_date_without_clock_remains_meaningful():
    constraints, inferred_departure, inferred_return, assumptions = (
        parse_time_constraints(
            "cotizar vuelos que lleguen a miami el 11 de febrero",
            today=TODAY,
        )
    )

    assert len(constraints) == 1
    constraint = constraints[0]
    assert constraint.leg_index == 0
    assert constraint.event.value == "arrival"
    assert str(constraint.date) == "2027-02-11"
    assert constraint.time_from is None
    assert constraint.time_to is None
    assert str(inferred_departure) == "2027-02-10"
