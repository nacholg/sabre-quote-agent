from app.services.commercial_quote_builder import _request_trip_shape


def test_legacy_null_trip_type_round_trip_is_inferred():
    legs, trip_type = _request_trip_shape(
        {
            "origin": "EZE",
            "destination": "MIA",
            "departure_date": "2026-09-19",
            "return_date": "2026-09-30",
            "trip_type": None,
        }
    )

    assert trip_type == "round_trip"
    assert [(x.origin, x.destination) for x in legs] == [
        ("EZE", "MIA"),
        ("MIA", "EZE"),
    ]


def test_legacy_null_trip_type_one_way_is_inferred():
    legs, trip_type = _request_trip_shape(
        {
            "origin": "EZE",
            "destination": "MIA",
            "departure_date": "2026-09-19",
            "return_date": None,
            "trip_type": None,
        }
    )

    assert trip_type == "one_way"
    assert len(legs) == 1


def test_explicit_multi_city_legs_are_preserved():
    legs, trip_type = _request_trip_shape(
        {
            "trip_type": "multi_city",
            "legs": [
                {
                    "origin": "EZE",
                    "destination": "MIA",
                    "departure_date": "2026-12-10",
                },
                {
                    "origin": "MIA",
                    "destination": "NYC",
                    "departure_date": "2026-12-25",
                },
                {
                    "origin": "JFK",
                    "destination": "EZE",
                    "departure_date": "2026-12-30",
                },
            ],
        }
    )

    assert trip_type == "multi_city"
    assert len(legs) == 3
