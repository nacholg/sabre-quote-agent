from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.models.api import QuoteSearchAPIRequest
from app.models.itinerary import FareOption, FlightSegment, ItineraryOption
from app.services import quote_service


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_request_direct_and_filters():
    request = QuoteSearchAPIRequest(
        environment="cert",
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        return_date="2026-09-30",
        direct=True,
        carriers=["AA", "LA"],
        excluded_carriers=["AR"],
    )
    search = request.to_search_request()
    assert search.max_stops == 0
    assert search.preferred_carriers == ["AA", "LA"]
    assert search.excluded_carriers == ["AR"]


def test_quotes_search_endpoint_with_stub(monkeypatch):
    async def fake_search_quote(request):
        fare = FareOption(
            cabin="economy",
            currency="USD",
            price_per_passenger=Decimal("1000"),
            total_price=Decimal("1000"),
            total_tax=Decimal("200"),
            fare_basis_codes=["TEST"],
            baggage=["1 pieza despachada de hasta 23 kg por pasajero."],
        )
        option = ItineraryOption(
            segments=[
                FlightSegment(
                    marketing_carrier="AA",
                    operating_carrier="AA",
                    flight_number="908",
                    departure_airport="EZE",
                    arrival_airport="MIA",
                    departure_country="AR",
                    arrival_country="US",
                    departure_at="2026-09-19T22:15:00-03:00",
                    arrival_at="2026-09-20T06:20:00-04:00",
                )
            ],
            fare=fare,
            fares_by_currency={"USD": fare},
            fare_options_by_currency={"USD": [fare]},
            source_index=0,
        )
        from app.models.api import QuoteSearchAPIResponse, RankedOption
        return QuoteSearchAPIResponse(
            environment="CERT",
            effective_currencies=["USD"],
            calls=[],
            result_count=1,
            options=[
                RankedOption(
                    rank=1,
                    score=Decimal("1"),
                    stops=0,
                    duration_minutes=545,
                    ranking_currency="USD",
                    ranking_price=Decimal("1000"),
                    itinerary=option,
                )
            ],
            client_quote="TEST QUOTE",
        )

    monkeypatch.setattr("app.main.search_quote", fake_search_quote)

    with TestClient(app) as client:
        response = client.post(
            "/quotes/search",
            json={
                "environment": "cert",
                "origin": "EZE",
                "destination": "MIA",
                "departure_date": "2026-09-19",
                "return_date": "2026-09-30",
                "direct": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["result_count"] == 1
    assert body["client_quote"] == "TEST QUOTE"
    assert body["options"][0]["itinerary"]["segments"][0]["flight_number"] == "908"
