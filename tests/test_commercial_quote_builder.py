from app.models.api import StoredQuoteRecord
from app.services.commercial_quote_builder import build_commercial_quote


def record() -> StoredQuoteRecord:
    return StoredQuoteRecord(
        quote_id="QTEST",
        created_at="2026-08-18T00:00:00",
        updated_at="2026-08-18T00:00:00",
        status="selected",
        selected_ranks=[2],
        source="agent",
        client_name="Cliente Demo",
        client_reference="REF-123",
        notes="Preferencia pasillo.",
        search_request={
            "origin": "EZE",
            "destination": "MIA",
            "departure_date": "2027-02-10",
            "return_date": "2027-02-20",
            "adults": 2,
            "children": 1,
            "infants": 0,
        },
        quote_response={
            "options": [
                {
                    "rank": 2,
                    "itinerary": {
                        "segments": [
                            {
                                "marketing_carrier": "AA",
                                "flight_number": "908",
                                "departure_airport": "EZE",
                                "arrival_airport": "MIA",
                                "departure_at": "2027-02-10T23:35:00-03:00",
                                "arrival_at": "2027-02-11T06:55:00-05:00",
                            }
                        ],
                        "fare": {
                            "cabin": "economy",
                            "currency": "USD",
                            "price_per_passenger": "500",
                            "total_price": "1500",
                            "baggage_pieces": 1,
                            "fare_basis_codes": ["OLX0N1M1"],
                            "last_ticket_date": "2026-08-20",
                        },
                        "fares_by_currency": {
                            "USD": {
                                "cabin": "economy",
                                "currency": "USD",
                                "price_per_passenger": "500",
                                "total_price": "1500",
                                "baggage_pieces": 1,
                                "fare_basis_codes": ["OLX0N1M1"],
                                "last_ticket_date": "2026-08-20",
                            }
                        },
                    },
                }
            ]
        },
    )


def test_build_commercial_quote_canonical_document():
    doc = build_commercial_quote(record())

    assert doc.quote_id == "QTEST"
    assert doc.client_name == "Cliente Demo"
    assert doc.client_reference == "REF-123"
    assert doc.origin == "EZE"
    assert doc.destination == "MIA"
    assert doc.departure_date == "2027-02-10"
    assert doc.return_date == "2027-02-20"
    assert doc.passenger_count == 3

    assert len(doc.options) == 1
    option = doc.options[0]
    assert option.source_rank == 2
    assert option.display_number == 1

    assert len(option.segments) == 1
    assert option.segments[0].marketing_carrier == "AA"
    assert option.segments[0].flight_number == "908"

    assert len(option.fares) == 1
    fare = option.fares[0]
    assert str(fare.price_per_passenger) == "500"
    assert fare.currency == "USD"
    assert fare.fare_basis_codes == ["OLX0N1M1"]
    assert fare.last_ticket_date == "2026-08-20"
    assert fare.baggage


def test_build_commercial_quote_requires_selection():
    rec = record().model_copy(update={"selected_ranks": []})

    try:
        build_commercial_quote(rec)
    except ValueError as exc:
        assert "opciones seleccionadas" in str(exc)
    else:
        raise AssertionError("Se esperaba ValueError")


def test_passenger_specs_take_precedence():
    rec = record()
    search = dict(rec.search_request)
    search["passengers"] = [
        {"type": "adult", "quantity": 2},
        {"type": "child", "quantity": 2, "age": 8},
    ]
    rec = rec.model_copy(update={"search_request": search})

    doc = build_commercial_quote(rec)

    assert doc.passenger_count == 4
