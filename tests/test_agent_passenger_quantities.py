from app.models.quote_request import PassengerKind
from app.services.agent_parser import _passengers


def as_map(text):
    passengers, warnings = _passengers(text)
    data = {
        (p.type, p.age): p.quantity
        for p in passengers
    }
    return data, warnings


def test_suffix_quantities_adt_child_threshold_and_infant():
    data, warnings = as_map(
        "ADT x 4, niños menores de 11 años x 4, 1 menor de 1 año"
    )
    assert data[(PassengerKind.ADULT, None)] == 4
    assert data[(PassengerKind.CHILD, 10)] == 4
    assert data[(PassengerKind.INFANT, 0)] == 1
    assert any("menor de 11" in warning.lower() for warning in warnings)


def test_explicit_sabre_ptcs_with_suffix_quantity():
    data, warnings = as_map("ADT x4, C09 x4, INF x1")
    assert data[(PassengerKind.ADULT, None)] == 4
    assert data[(PassengerKind.CHILD, 9)] == 4
    assert data[(PassengerKind.INFANT, None)] == 1
    assert warnings == []


def test_prefix_quantity_with_exact_child_age():
    data, _ = as_map("4 adultos, 4 niños de 9 años, 1 infante")
    assert data[(PassengerKind.ADULT, None)] == 4
    assert data[(PassengerKind.CHILD, 9)] == 4
    assert data[(PassengerKind.INFANT, None)] == 1


def test_suffix_quantity_with_exact_child_age():
    data, _ = as_map("adultos x4, niños de 9 años x4, bebé x1")
    assert data[(PassengerKind.ADULT, None)] == 4
    assert data[(PassengerKind.CHILD, 9)] == 4
    assert data[(PassengerKind.INFANT, None)] == 1


def test_age_list_keeps_distinct_child_ptcs():
    data, _ = as_map("2 adultos, niños de 9 y 7 años")
    assert data[(PassengerKind.ADULT, None)] == 2
    assert data[(PassengerKind.CHILD, 9)] == 1
    assert data[(PassengerKind.CHILD, 7)] == 1


def test_child_count_without_age_is_rejected():
    try:
        _passengers("2 adultos y 4 niños")
    except ValueError as exc:
        assert "sin edad" in str(exc).lower()
    else:
        raise AssertionError("Debió rechazar menores sin edad.")
