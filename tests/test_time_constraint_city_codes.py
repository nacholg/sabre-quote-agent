from datetime import date, datetime
from types import SimpleNamespace

from app.models.quote_request import SearchLeg
from app.services.location_resolver import clear_location_resolver_cache
from app.services.reference_repository import get_reference_repository, seed_reference_data
from app.services.time_constraint_filter import _split_option_legs


def _seed():
    seed_reference_data(get_reference_repository())
    clear_location_resolver_cache()


def _segment(origin, destination, hour):
    return SimpleNamespace(
        departure_airport=origin,
        arrival_airport=destination,
        departure_at=datetime(2026, 12, 25, hour, 0),
        arrival_at=datetime(2026, 12, 25, hour + 1, 0),
    )


def test_city_destination_matches_actual_airport():
    _seed()
    option = SimpleNamespace(segments=[_segment('MIA', 'JFK', 10)])
    legs = [SearchLeg(origin='MIA', destination='NYC', departure_date=date(2026,12,25))]
    split = _split_option_legs(option, legs)
    assert len(split) == 1
    assert split[0][-1].arrival_airport == 'JFK'


def test_city_origin_matches_actual_airport():
    _seed()
    option = SimpleNamespace(segments=[_segment('EZE', 'MIA', 8)])
    legs = [SearchLeg(origin='BUE', destination='MIA', departure_date=date(2026,12,25))]
    split = _split_option_legs(option, legs)
    assert len(split) == 1
    assert split[0][0].departure_airport == 'EZE'


def test_specific_airport_does_not_match_other_airport_in_same_city():
    _seed()
    option = SimpleNamespace(segments=[_segment('MIA', 'EWR', 10)])
    legs = [SearchLeg(origin='MIA', destination='JFK', departure_date=date(2026,12,25))]
    assert _split_option_legs(option, legs) == []
