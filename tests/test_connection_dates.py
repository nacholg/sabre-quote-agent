from app.services.normalizer import normalize_bfm_response


def test_connecting_segment_after_midnight_rolls_to_next_day():
    payload = {
        "groupedItineraryResponse": {
            "scheduleDescs": [
                {
                    "id": 1,
                    "departure": {"airport": "EZE", "time": "16:55:00-03:00"},
                    "arrival": {"airport": "LIM", "time": "19:40:00-05:00"},
                    "carrier": {"marketing": "LA", "operating": "LA", "marketingFlightNumber": 7648},
                    "elapsedTime": 285,
                },
                {
                    "id": 2,
                    "departure": {"airport": "LIM", "time": "00:15:00-05:00"},
                    "arrival": {"airport": "MIA", "time": "07:20:00-04:00"},
                    "carrier": {"marketing": "LA", "operating": "LA", "marketingFlightNumber": 2480},
                    "elapsedTime": 365,
                },
            ],
            "legDescs": [{"id": 10, "schedules": [{"ref": 1}, {"ref": 2}]}],
            "fareComponentDescs": [],
            "baggageAllowanceDescs": [],
            "taxDescs": [],
            "itineraryGroups": [
                {
                    "groupDescription": {"legDescriptions": [{"departureDate": "2026-09-19"}]},
                    "itineraries": [
                        {
                            "legs": [{"ref": 10}],
                            "pricingInformation": [
                                {
                                    "fare": {
                                        "passengerInfoList": [
                                            {
                                                "passengerInfo": {
                                                    "passengerTotalFare": {"totalFare": 400, "currency": "USD"},
                                                    "fareComponents": [],
                                                }
                                            }
                                        ],
                                        "totalFare": {"totalPrice": 400, "currency": "USD"},
                                    }
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }
    options = normalize_bfm_response(payload)
    assert len(options) == 1
    assert options[0].segments[1].departure_at.date().isoformat() == "2026-09-20"
    assert options[0].segments[1].arrival_at > options[0].segments[1].departure_at
