from app.models.quote_request import QuoteSearchRequest
from app.sabre.shopping import build_bfm_request, extract_bfm_diagnostics


def test_official_profile_matches_minimal_openapi_shape() -> None:
    request = QuoteSearchRequest(
        origin="eze",
        destination="mia",
        departure_date="2026-09-19",
        adults=1,
        request_profile="official",
    )
    payload = build_bfm_request(request, "RY3A")["OTA_AirLowFareSearchRQ"]
    assert payload["Version"] == "5"
    assert payload["OriginDestinationInformation"][0]["DepartureDateTime"] == "2026-09-19T12:00:00"
    assert payload["TPA_Extensions"]["IntelliSellTransaction"]["RequestType"]["Name"] == "50ITINS"
    assert "CabinPref" not in payload["TravelPreferences"]
    assert "PriceRequestInformation" not in payload["TravelerInfoSummary"]


def test_standard_profile_adds_shopping_preferences() -> None:
    request = QuoteSearchRequest(
        origin="eze",
        destination="dfw",
        departure_date="2026-09-19",
        return_date="2026-09-28",
        adults=2,
        cabin="BUSINESS",
        request_profile="standard",
    )
    payload = build_bfm_request(request)["OTA_AirLowFareSearchRQ"]
    assert len(payload["OriginDestinationInformation"]) == 2
    assert payload["TravelPreferences"]["CabinPref"][0]["Cabin"] == "C"
    assert payload["TravelPreferences"]["Baggage"]["RequestType"] == "C"
    assert payload["TravelerInfoSummary"]["SeatsRequested"] == [2]
    assert payload["AvailableFlightsOnly"] is True


def test_child_code_uses_age_specific_ptc() -> None:
    request = QuoteSearchRequest(
        origin="eze",
        destination="mia",
        departure_date="2026-09-19",
        children=1,
        child_age=8,
    )
    passengers = build_bfm_request(request)["OTA_AirLowFareSearchRQ"]["TravelerInfoSummary"]["AirTravelerAvail"][0]["PassengerTypeQuantity"]
    assert {"Code": "C08", "Quantity": 1} in passengers


def test_extract_diagnostics() -> None:
    response = {
        "groupedItineraryResponse": {
            "version": "7.2.2",
            "statistics": {"itineraryCount": 0},
            "messages": [
                {"code": "TRANSACTIONID", "text": "123"},
                {"code": "NAV", "text": "No Availability"},
            ],
        }
    }
    result = extract_bfm_diagnostics(response)
    assert result["transaction_id"] == "123"
    assert result["no_availability"] is True
