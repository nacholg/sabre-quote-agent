from app.models.quote_request import FarePreference, PassengerKind, PassengerSpec, QuoteSearchRequest, SearchLeg
from app.sabre.shopping import build_bfm_request


def test_open_jaw_explicit_legs():
    search = QuoteSearchRequest(
        origin="EZE", destination="MIA", departure_date="2026-09-19",
        legs=[
            SearchLeg(origin="EZE", destination="MIA", departure_date="2026-09-19"),
            SearchLeg(origin="JFK", destination="EZE", departure_date="2026-09-30"),
        ],
    )
    body = build_bfm_request(search, "RY3A")["OTA_AirLowFareSearchRQ"]
    ods = body["OriginDestinationInformation"]
    assert [(x["OriginLocation"]["LocationCode"], x["DestinationLocation"]["LocationCode"]) for x in ods] == [
        ("EZE", "MIA"), ("JFK", "EZE")
    ]
    assert [x["RPH"] for x in ods] == ["1", "2"]


def test_circle_trip_three_legs():
    search = QuoteSearchRequest(
        origin="EZE", destination="MIA", departure_date="2026-09-19",
        legs=[
            SearchLeg(origin="EZE", destination="MIA", departure_date="2026-09-19"),
            SearchLeg(origin="MIA", destination="JFK", departure_date="2026-09-25"),
            SearchLeg(origin="JFK", destination="EZE", departure_date="2026-09-30"),
        ],
    )
    body = build_bfm_request(search, "RY3A")["OTA_AirLowFareSearchRQ"]
    assert len(body["OriginDestinationInformation"]) == 3


def test_baggage_preference_requires_free_piece():
    search = QuoteSearchRequest(
        origin="EZE", destination="MIA", departure_date="2026-09-19",
        fare_preference=FarePreference.BAGGAGE,
    )
    body = build_bfm_request(search, "RY3A")["OTA_AirLowFareSearchRQ"]
    assert body["TravelPreferences"]["Baggage"]["FreePieceRequired"] is True


def test_branded_requests_multiple_brands():
    search = QuoteSearchRequest(
        origin="EZE", destination="MIA", departure_date="2026-09-19",
        fare_preference=FarePreference.BRANDED,
    )
    body = build_bfm_request(search, "RY3A")["OTA_AirLowFareSearchRQ"]
    branded = body["TravelerInfoSummary"]["PriceRequestInformation"]["TPA_Extensions"]["BrandedFareIndicators"]
    assert branded["MultipleBrandedFares"] is True
    assert branded["ReturnBrandAncillaries"] is True
    assert branded["UpsellLimit"] == 3


def test_bfm_uses_age_specific_child_ptcs():
    search = QuoteSearchRequest(
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        passengers=[
            PassengerSpec(type=PassengerKind.ADULT, quantity=2),
            PassengerSpec(type=PassengerKind.CHILD, age=9, quantity=1),
            PassengerSpec(type=PassengerKind.CHILD, age=4, quantity=1),
        ],
    )
    body = build_bfm_request(search, "RY3A")["OTA_AirLowFareSearchRQ"]
    ptcs = body["TravelerInfoSummary"]["AirTravelerAvail"][0]["PassengerTypeQuantity"]
    assert ptcs == [
        {"Code": "ADT", "Quantity": 2},
        {"Code": "C09", "Quantity": 1},
        {"Code": "C04", "Quantity": 1},
    ]
    assert body["TravelerInfoSummary"]["SeatsRequested"] == [4]


def test_bfm_infant_does_not_add_seat():
    search = QuoteSearchRequest(
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        passengers=[
            PassengerSpec(type=PassengerKind.ADULT, quantity=2),
            PassengerSpec(type=PassengerKind.INFANT, quantity=1),
        ],
    )
    body = build_bfm_request(search, "RY3A")["OTA_AirLowFareSearchRQ"]
    assert body["TravelerInfoSummary"]["SeatsRequested"] == [2]


def test_refundable_preference_requests_brand_ancillaries():
    search = QuoteSearchRequest(
        origin="EZE", destination="MIA", departure_date="2026-09-19",
        fare_preference=FarePreference.REFUNDABLE,
    )
    body = build_bfm_request(search, "RY3A")["OTA_AirLowFareSearchRQ"]
    branded = body["TravelerInfoSummary"]["PriceRequestInformation"]["TPA_Extensions"]["BrandedFareIndicators"]
    assert branded["MultipleBrandedFares"] is True
    assert branded["ReturnBrandAncillaries"] is True
