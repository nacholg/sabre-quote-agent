from app.services.normalizer import normalize_bfm_response
from app.services.quote_renderer import render_client_quote


def branded_response():
    return {
        "groupedItineraryResponse": {
            "scheduleDescs": [
                {"id": 1, "elapsedTime": 500,
                 "departure": {"airport": "EZE", "country": "AR", "time": "13:00:00-03:00"},
                 "arrival": {"airport": "MIA", "country": "US", "time": "20:00:00-04:00"},
                 "carrier": {"marketing": "LA", "marketingFlightNumber": 542, "operating": "LA"}}
            ],
            "legDescs": [{"id": 1, "schedules": [{"ref": 1}]}],
            "baggageAllowanceDescs": [
                {"id": 1, "pieceCount": 0},
                {"id": 2, "pieceCount": 1, "description1": "UP TO 50 POUNDS/23 KILOGRAMS"},
            ],
            "brandFeatureDescs": [
                {"id": 1, "application": "C", "serviceType": "C", "serviceGroup": "BG", "subCode": "0CC", "commercialName": "FIRST BAG UP TO 23KG"},
                {"id": 2, "application": "F", "serviceType": "C", "serviceGroup": "BG", "subCode": "0CC", "commercialName": "FIRST BAG UP TO 23KG"},
                {"id": 3, "application": "C", "serviceType": "Z", "serviceGroup": "BF", "subCode": "06I", "commercialName": "CHANGE BEFORE DEPARTURE"},
            ],
            "fareComponentDescs": [
                {"id": 1, "fareBasisCode": "LIGHT", "cabinCode": "Y", "brand": {"code": "SL", "brandName": "LIGHT", "programCode": "CFFLA"}},
                {"id": 2, "fareBasisCode": "STD", "cabinCode": "Y", "brand": {"code": "KM", "brandName": "STANDARD", "programCode": "CFFLA"}},
            ],
            "taxDescs": [],
            "itineraryGroups": [{
                "groupDescription": {"legDescriptions": [{"departureDate": "2026-09-19"}]},
                "itineraries": [{
                    "id": 1, "legs": [{"ref": 1}],
                    "pricingInformation": [
                        {"fare": {"lastTicketDate": "2026-08-15", "passengerInfoList": [{"passengerInfo": {
                            "nonRefundable": True,
                            "fareComponents": [{"ref": 1, "segments": [{"segment": {"bookingCode": "O", "cabinCode": "Y"}}], "brandFeatures": [{"ref": 1}, {"ref": 3}]}],
                            "passengerTotalFare": {"totalFare": 701.84, "totalTaxAmount": 100, "currency": "USD"},
                            "baggageInformation": [{"provisionType": "A", "allowance": {"ref": 1}}],
                        }}], "totalFare": {"totalPrice": 701.84, "currency": "USD"}}},
                        {"soldOut": {"status": "F"}},
                        {"fare": {"lastTicketDate": "2026-08-15", "passengerInfoList": [{"passengerInfo": {
                            "nonRefundable": True,
                            "fareComponents": [{"ref": 2, "segments": [{"segment": {"bookingCode": "O", "cabinCode": "Y"}}], "brandFeatures": [{"ref": 2}, {"ref": 3}]}],
                            "passengerTotalFare": {"totalFare": 792.84, "totalTaxAmount": 120, "currency": "USD"},
                            "baggageInformation": [{"provisionType": "A", "allowance": {"ref": 2}}],
                        }}], "totalFare": {"totalPrice": 792.84, "currency": "USD"}}},
                    ]
                }]
            }]
        }
    }


def test_multiple_brands_are_preserved_and_rendered():
    options = normalize_bfm_response(branded_response())
    assert len(options) == 1
    fares = options[0].fare_options_by_currency["USD"]
    assert [(f.brand_name, f.baggage_pieces) for f in fares] == [("LIGHT", 0), ("STANDARD", 1)]
    assert fares[1].brand_features[0].status == "included"
    quote = render_client_quote(options)
    assert "LIGHT — USD 701.84" in quote
    assert "STANDARD — USD 792.84" in quote
    assert "No incluye equipaje despachado" in quote
    assert "1 pieza despachada de hasta 23 kg" in quote

from decimal import Decimal
from datetime import datetime

from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services.normalizer import merge_cabin_itineraries


def _fare(cabin: str, brand: str, price: str, bags: int | None = None) -> FareOption:
    return FareOption(
        cabin=cabin,
        currency="USD",
        price_per_passenger=Decimal(price),
        total_price=Decimal(price),
        brand_name=brand,
        baggage_pieces=bags,
        baggage=[] if bags is None else (["No incluye equipaje despachado."] if bags == 0 else [f"{bags} piezas despachadas por pasajero."]),
    )


def _segment() -> FlightSegment:
    return FlightSegment(
        marketing_carrier="AA", flight_number="908", departure_airport="EZE", arrival_airport="MIA",
        departure_country="AR", arrival_country="US",
        departure_at=datetime.fromisoformat("2026-09-19T22:15:00-03:00"),
        arrival_at=datetime.fromisoformat("2026-09-20T06:20:00-04:00"),
    )


def test_commercial_renderer_hides_main_plus_and_keeps_four_levels():
    main = _fare("economy", "MAIN CABIN", "1133.73", 1)
    main_plus = _fare("economy", "MAIN PLUS", "1480.43", 2)
    flex = _fare("economy", "MAIN CABIN FLEXIBLE", "1401.23", 1)
    premium = _fare("premium economy", "PREMIUM ECONOMY", "2439.13", 2)
    business = _fare("business", "BUSINESS", "4200.00", 2)
    option = ItineraryOption(
        segments=[_segment()], fare=main, fares_by_currency={"USD": main},
        fare_options_by_currency={"USD": [main, main_plus, flex, premium, business]},
    )
    quote = render_client_quote([option])
    assert "MAIN CABIN — USD 1,133.73" in quote
    assert "MAIN CABIN FLEXIBLE — USD 1,401.23" in quote
    assert "PREMIUM ECONOMY — USD 2,439.13" in quote
    assert "BUSINESS — USD 4,200.00" in quote
    assert "MAIN PLUS" not in quote
    assert "\nECONOMY\n" not in quote


def test_business_fare_only_merges_into_exact_matching_flights():
    main = _fare("economy", "MAIN CABIN", "1000.00", 1)
    business = _fare("business", "BUSINESS", "4000.00", 2)
    base = ItineraryOption(
        segments=[_segment()], fare=main, fares_by_currency={"USD": main},
        fare_options_by_currency={"USD": [main]},
    )
    companion = ItineraryOption(
        segments=[_segment()], fare=business, fares_by_currency={"USD": business},
        fare_options_by_currency={"USD": [business]},
    )
    merge_cabin_itineraries([base], [companion], cabins={"business"})
    assert [f.cabin for f in base.fare_options_by_currency["USD"]] == ["economy", "business"]


def test_branded_conditions_are_product_specific_and_not_global():
    from app.models.itinerary import BrandFeature

    main = _fare("economy", "MAIN CABIN", "1133.73", 1)
    main.non_refundable = True
    main.last_ticket_date = "2026-08-15"
    main.brand_features = [
        BrandFeature(application="C", commercial_name="CHANGE BEFORE DEPARTURE"),
        BrandFeature(application="D", commercial_name="REFUND BEFORE DEPARTURE"),
    ]

    flex = _fare("economy", "MAIN CABIN FLEXIBLE", "1401.23", 1)
    flex.non_refundable = False
    flex.last_ticket_date = "2026-08-15"
    flex.brand_features = [
        BrandFeature(application="F", commercial_name="CHANGE BEFORE DEPARTURE"),
        BrandFeature(application="F", commercial_name="REFUND BEFORE DEPARTURE"),
    ]

    option = ItineraryOption(
        segments=[_segment()], fare=main, fares_by_currency={"USD": main},
        fare_options_by_currency={"USD": [main, flex]},
    )
    quote = render_client_quote([option])
    assert "MAIN CABIN — USD 1,133.73" in quote
    assert "Cambios: permitidos con cargo según atributo branded." in quote
    assert "Devoluciones: no permitidas según atributo branded." in quote
    assert "MAIN CABIN FLEXIBLE — USD 1,401.23" in quote
    assert "Cambios: permitidos sin cargo según atributo branded." in quote
    assert "Devoluciones: permitidas según atributo branded." in quote
    # No itinerary-wide refundability statement should remain below the brands.
    conditions = quote.split("\nCondiciones\n", 1)[1]
    assert "Tarifa no reembolsable." not in conditions
    assert "Tarifa reembolsable" not in conditions
    assert "Emitir hasta el 2026-08-15" in conditions
    assert "Tarifas sujetas a disponibilidad" in conditions


def test_different_ticket_dates_are_kept_with_each_brand():
    main = _fare("economy", "MAIN CABIN", "1000.00", 1)
    main.last_ticket_date = "2026-08-15"
    flex = _fare("economy", "MAIN CABIN FLEXIBLE", "1200.00", 1)
    flex.last_ticket_date = "2026-08-17"
    option = ItineraryOption(
        segments=[_segment()], fare=main, fares_by_currency={"USD": main},
        fare_options_by_currency={"USD": [main, flex]},
    )
    quote = render_client_quote([option])
    assert "Emitir hasta el 2026-08-15" in quote
    assert "Emitir hasta el 2026-08-17" in quote
    conditions = quote.split("\nCondiciones\n", 1)[1]
    assert "Emitir hasta el" not in conditions
