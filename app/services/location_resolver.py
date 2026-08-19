from __future__ import annotations

from functools import lru_cache

from app.services.reference_repository import get_reference_repository


@lru_cache(maxsize=512)
def airports_for_location(code: str) -> tuple[str, ...]:
    requested = code.upper().strip()
    if len(requested) != 3:
        return ()

    repo = get_reference_repository()
    if repo.airport(requested) is not None:
        return (requested,)

    airports = tuple(repo.airports_for_city(requested))
    if airports:
        return airports

    return (requested,)


def is_city_location(code: str) -> bool:
    requested = code.upper().strip()
    if len(requested) != 3:
        return False

    repo = get_reference_repository()
    airports = tuple(repo.airports_for_city(requested))
    return repo.airport(requested) is None and bool(airports)


def locations_equivalent(left: str, right: str) -> bool:
    "Return True when two requested location codes represent overlapping airports."
    left_airports = set(airports_for_location(left))
    right_airports = set(airports_for_location(right))
    return bool(left_airports and right_airports and left_airports & right_airports)


def location_matches(requested: str, actual_airport: str) -> bool:
    actual = actual_airport.upper().strip()
    return actual in airports_for_location(requested)


def clear_location_resolver_cache() -> None:
    airports_for_location.cache_clear()
