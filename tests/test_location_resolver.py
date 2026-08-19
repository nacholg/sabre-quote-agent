from app.services.location_resolver import (
    airports_for_location, clear_location_resolver_cache,
    is_city_location, location_matches,
)
from app.services.reference_repository import (
    get_reference_repository, seed_reference_data,
)


def _seed():
    repo = get_reference_repository()
    seed_reference_data(repo)
    clear_location_resolver_cache()


def test_multi_airport_city_codes_expand_to_airports():
    _seed()
    assert set(airports_for_location("NYC")) == {"JFK", "EWR", "LGA"}
    assert set(airports_for_location("LON")) == {"LHR", "LCY", "STN", "LGW"}
    assert set(airports_for_location("TYO")) == {"HND", "NRT"}
    assert set(airports_for_location("BUE")) == {"AEP", "EZE"}
    assert set(airports_for_location("SAO")) == {"CGH", "VCP", "GRU"}
    assert set(airports_for_location("RIO")) == {"GIG", "SDU"}


def test_exact_airport_stays_exact():
    _seed()
    assert airports_for_location("JFK") == ("JFK",)
    assert airports_for_location("EZE") == ("EZE",)
    assert airports_for_location("MIA") == ("MIA",)


def test_location_matches_city_to_actual_airport():
    _seed()
    assert location_matches("NYC", "JFK")
    assert location_matches("NYC", "EWR")
    assert location_matches("LON", "LCY")
    assert location_matches("BUE", "AEP")
    assert not location_matches("NYC", "MIA")
    assert not location_matches("LON", "CDG")


def test_multi_airport_codes_are_identified_as_city_locations():
    _seed()
    assert is_city_location("NYC")
    assert is_city_location("LON")
    assert is_city_location("BUE")
    assert not is_city_location("JFK")
    assert not is_city_location("MIA")
