from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse, RankedOption
from app.models.itinerary import BrandFeature, FareOption, FlightSegment, ItineraryOption
from app.services.fare_rule_reliability import audit_fare
from app.services.quote_repository import QuoteRepository, reset_quote_repository_for_tests


def _fare(
    *,
    brand: str,
    non_refundable: bool | None,
    change_application: str | None = None,
    refund_application: str | None = None,
) -> FareOption:
    features = []
    if change_application:
        features.append(
            BrandFeature(
                application=change_application,
                commercial_name="CHANGE BEFORE DEPARTURE",
            )
        )
    if refund_application:
        features.append(
            BrandFeature(
                application=refund_application,
                commercial_name="REFUND BEFORE DEPARTURE",
            )
        )
    return FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal("1000"),
        total_price=Decimal("1000"),
        brand_name=brand,
        non_refundable=non_refundable,
        baggage_pieces=1,
        baggage=["1 pieza despachada por pasajero."],
        last_ticket_date="2026-08-20",
        brand_features=features,
    )


def test_explicit_branded_rules_are_high_confidence():
    fare = _fare(
        brand="FLEX",
        non_refundable=False,
        change_application="F",
        refund_application="F",
    )
    audit = audit_fare(fare)
    assert audit.changes.status == "included"
    assert audit.changes.source == "brand_feature"
    assert audit.changes.confidence == "high"
    assert audit.refunds.status == "allowed"
    assert audit.refunds.source == "brand_feature"
    assert audit.refunds.confidence == "high"


def test_non_refundable_true_is_reliable_negative_flag():
    fare = _fare(brand="BASE", non_refundable=True)
    audit = audit_fare(fare)
    assert audit.refunds.status == "not_allowed"
    assert audit.refunds.source == "fare_flag"
    assert audit.refunds.confidence == "high"


def test_non_refundable_false_does_not_claim_refund_is_allowed():
    fare = _fare(brand="FLEX", non_refundable=False)
    audit = audit_fare(fare)
    assert audit.refunds.status == "unknown"
    assert audit.refunds.source == "fare_flag"
    assert audit.refunds.confidence == "medium"
    assert "Confirmar fare rules" in audit.refunds.text


def _stored_quote(repo: QuoteRepository) -> str:
    fare = _fare(brand="FLEX", non_refundable=False)
    itinerary = ItineraryOption(
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
    )
    request = QuoteSearchAPIRequest(
        environment="cert",
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        adults=1,
        currency="USD",
    )
    response = QuoteSearchAPIResponse(
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
                itinerary=itinerary,
            )
        ],
        client_quote="TEST",
    )
    return repo.create(request=request, response=response)


def test_fare_rules_endpoint_reports_external_lookup_needed(tmp_path, monkeypatch):
    db_path = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db_path)
    quote_id = _stored_quote(repo)
    repo.select(quote_id, [1])
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        response = client.get(f"/quotes/{quote_id}/fare-rules")

    assert response.status_code == 200
    body = response.json()
    assert body["quote_id"] == quote_id
    assert body["requires_external_rule_lookup"] is True
    fare = body["options"][0]["fares"][0]
    assert fare["refunds"]["status"] == "unknown"
    assert fare["changes"]["status"] == "unknown"


def test_commercial_renderer_does_not_overclaim_refundability(tmp_path, monkeypatch):
    db_path = tmp_path / "quotes.db"
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))
    reset_quote_repository_for_tests()

    repo = QuoteRepository(db_path)
    quote_id = _stored_quote(repo)
    repo.select(quote_id, [1])
    reset_quote_repository_for_tests()

    with TestClient(app) as client:
        response = client.get(f"/quotes/{quote_id}/whatsapp")

    assert response.status_code == 200
    assert "Devoluciones: confirmar reglas tarifarias." in response.text
    assert "Devoluciones: permitidas según las condiciones" not in response.text
