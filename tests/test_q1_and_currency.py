from datetime import datetime, timezone
from decimal import Decimal

from app.models.itinerary import FareOption, FlightSegment, ItineraryOption, TaxDetail
from app.services.normalizer import merge_currency_itineraries
from app.services.quote_renderer import render_client_quote


def _segment():
    return FlightSegment(
        marketing_carrier="AA",
        operating_carrier="AA",
        flight_number="900",
        departure_airport="EZE",
        arrival_airport="MIA",
        departure_country="AR",
        arrival_country="US",
        departure_at=datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc),
        arrival_at=datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc),
    )


def _fare(currency: str, amount: str, q1: str | None = None):
    return FareOption(
        cabin="economy",
        currency=currency,
        price_per_passenger=Decimal(amount),
        pricing_modifier="MARS" if currency == "ARS" else "MUSD",
        q1_amount=Decimal(q1) if q1 else None,
        q1_currency="ARS" if q1 else None,
        taxes=[TaxDetail(code="Q1", amount=Decimal(q1), currency="ARS")] if q1 else [],
    )


def test_merge_and_render_usd_ars_with_q1():
    usd = ItineraryOption(segments=[_segment()], fare=_fare("USD", "1200"))
    usd.fares_by_currency = {"USD": usd.fare}
    ars = ItineraryOption(segments=[_segment()], fare=_fare("ARS", "1800000", "125000"))
    ars.fares_by_currency = {"ARS": ars.fare}

    merged = merge_currency_itineraries([usd], [ars])
    assert set(merged[0].fares_by_currency) == {"USD", "ARS"}
    text = render_client_quote(merged)
    assert "USD 1,200.00" in text
    assert "ARS 1,800,000.00" in text
    assert "Impuesto Q1 incluido: ARS 125,000.00" in text
