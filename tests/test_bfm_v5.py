from app.models.quote_request import QuoteSearchRequest
from app.sabre.shopping import build_bfm_request
from app.services.normalizer import normalize_bfm_response
from app.services.quote_renderer import render_client_quote


def sample_response() -> dict:
    return {
        "groupedItineraryResponse": {
            "scheduleDescs": [
                {
                    "id": 1,
                    "elapsedTime": 535,
                    "departure": {"airport": "EZE", "time": "20:30:00-03:00"},
                    "arrival": {"airport": "MIA", "time": "05:10:00-04:00"},
                    "carrier": {"marketing": "AA", "marketingFlightNumber": 908, "operating": "AA"},
                }
            ],
            "fareComponentDescs": [{"id": 1, "fareBasisCode": "OLN0A0", "cabinCode": "Y"}],
            "baggageAllowanceDescs": [{"id": 1, "pieceCount": 2}],
            "legDescs": [{"id": 1, "schedules": [{"ref": 1}]}],
            "itineraryGroups": [
                {
                    "groupDescription": {"legDescriptions": [{"departureDate": "2026-06-19"}]},
                    "itineraries": [
                        {
                            "legs": [{"ref": 1}],
                            "pricingInformation": [
                                {
                                    "fare": {
                                        "validatingCarrierCode": "AA",
                                        "lastTicketDate": "2026-06-10",
                                        "passengerInfoList": [
                                            {
                                                "passengerInfo": {
                                                    "passengerType": "ADT",
                                                    "nonRefundable": False,
                                                    "fareComponents": [
                                                        {
                                                            "ref": 1,
                                                            "segments": [
                                                                {"segment": {"bookingCode": "O", "cabinCode": "Y", "seatsAvailable": 9}}
                                                            ],
                                                        }
                                                    ],
                                                    "passengerTotalFare": {
                                                        "totalFare": 1234,
                                                        "totalTaxAmount": 234,
                                                        "currency": "USD",
                                                    },
                                                    "baggageInformation": [{"allowance": {"ref": 1}}],
                                                }
                                            }
                                        ],
                                        "totalFare": {"totalPrice": 1234, "currency": "USD"},
                                    }
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }


def test_builds_v5_request() -> None:
    search = QuoteSearchRequest(origin="EZE", destination="MIA", departure_date="2026-06-19")
    payload = build_bfm_request(search, "RY3A")
    request = payload["OTA_AirLowFareSearchRQ"]
    assert request["Version"] == "5"
    assert request["POS"]["Source"][0]["PseudoCityCode"] == "RY3A"


def test_normalizes_and_renders() -> None:
    options = normalize_bfm_response(sample_response())
    assert len(options) == 1
    assert options[0].fare.price_per_passenger == 1234
    assert options[0].fare.baggage_pieces == 2
    text = render_client_quote(options)
    assert "1 AA 908" in text
    assert "19JUN" in text
    assert "20JUN" in text
    assert "USD 1,234.00" in text
    assert "2 piezas despachadas" in text
