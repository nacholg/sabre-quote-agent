from decimal import Decimal

from app.models.commercial_quote import (
    CommercialQuoteDocument,
    CommercialQuoteFare,
    CommercialQuoteOption,
    CommercialQuoteSegment,
)
from app.services.commercial_renderer import (
    render_email_document,
    render_whatsapp_document,
)


def document() -> CommercialQuoteDocument:
    return CommercialQuoteDocument(
        quote_id="Q-123",
        origin="EZE",
        destination="MIA",
        departure_date="2027-02-10",
        return_date="2027-02-20",
        passenger_count=2,
        options=[
            CommercialQuoteOption(
                source_rank=4,
                display_number=1,
                segments=[
                    CommercialQuoteSegment(
                        marketing_carrier="AA",
                        flight_number="908",
                        departure_airport="EZE",
                        arrival_airport="MIA",
                        departure_at="2027-02-10T23:35:00-03:00",
                        arrival_at="2027-02-11T06:55:00-05:00",
                    )
                ],
                fares=[
                    CommercialQuoteFare(
                        cabin="economy",
                        brand_name="MAIN CABIN",
                        currency="USD",
                        price_per_passenger=Decimal("500"),
                        baggage="1 pieza despachada",
                        conditions=["Cambios: con cargo"],
                        fare_basis_codes=["OLX0N1M1"],
                    )
                ],
            )
        ],
    )


def test_whatsapp_renders_from_canonical_document():
    text = render_whatsapp_document(document())

    assert "*EZE – MIA*" in text
    assert "*Opción 1*" in text
    assert "AA 908 10FEB EZE/MIA 2335 0655 11FEB" in text
    assert "MAIN CABIN" in text
    assert "USD 500" in text
    assert "Equipaje: 1 pieza despachada" in text
    assert "Cambios: con cargo" in text
    assert "Referencia: Q-123" in text


def test_email_renders_from_same_canonical_document():
    html = render_email_document(document())

    assert "<h2>EZE – MIA</h2>" in html
    assert "Opción 1" in html
    assert "AA 908" in html
    assert "MAIN CABIN" in html
    assert "Equipaje: 1 pieza despachada" in html
    assert "Cambios: con cargo" in html
    assert "Referencia: Q-123" in html
