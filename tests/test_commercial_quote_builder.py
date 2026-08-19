from decimal import Decimal
from unittest.mock import patch

from app.models.api import FareRuleAuditResponse, FareRuleCommercialSummary, FareRuleDatum, FareRuleFareAudit, FareRuleOptionAudit, StoredQuoteRecord
from app.services.commercial_quote_builder import build_commercial_quote


def _datum(text):
    return FareRuleDatum(status="unknown", source="air_rules", confidence="high", text=text)


def _record():
    itinerary = {
        "segments": [{"marketing_carrier":"AA","flight_number":"908","departure_airport":"EZE","arrival_airport":"MIA","departure_at":"2026-09-19T22:15:00","arrival_at":"2026-09-20T06:20:00"}],
        "fare": {
            "cabin":"economy","currency":"USD","price_per_passenger":"1143.33","total_price":"2286.66",
            "fare_basis_codes":["QLN0AHM1"],"validating_carrier":"AA","brand_code":"MAIN","brand_name":"MAIN CABIN",
            "baggage":["1 pieza despachada de hasta 23 kg por pasajero."],
            "passenger_prices":[
                {"passenger_type":"ADT","quantity":1,"currency":"USD","unit_price":"1143.33","total_price":"1143.33"},
                {"passenger_type":"C10","quantity":1,"age":10,"currency":"USD","unit_price":"1143.33","total_price":"1143.33"},
            ],
        },
        "fares_by_currency": {}, "fare_options_by_currency": {},
    }
    return StoredQuoteRecord(
        quote_id="Q-TEST", created_at="2026-08-19T12:00:00", updated_at="2026-08-19T12:00:00",
        status="selected", selected_ranks=[1], source="agent", client_name="Cliente Test", client_reference="REF-1",
        search_request={"origin":"EZE","destination":"MIA","departure_date":"2026-09-19","return_date":"2026-09-30","trip_type":"round_trip"},
        quote_response={"environment":"CERT","options":[{"rank":1,"score":"100","stops":0,"duration_minutes":540,"commercial_labels":["recommended"],"itinerary":itinerary}]},
    )


def _audit():
    return FareRuleAuditResponse(
        quote_id="Q-TEST", selected_only=True,
        options=[FareRuleOptionAudit(rank=1, fares=[FareRuleFareAudit(
            cabin="economy", brand_name="MAIN CABIN", brand_code="MAIN", currency="USD",
            price_per_passenger=Decimal("1143.33"), baggage=_datum("Equipaje"), changes=_datum("Cambios"),
            refunds=_datum("Devoluciones"), ticketing=_datum("Emisión"),
            commercial_summary=FareRuleCommercialSummary(
                baggage="1 pieza despachada de hasta 23 kg.", changes="Cambios permitidos con diferencia tarifaria.",
                refunds="Devolución no permitida.", no_show="No-show no permitido.", ticketing="Emitir antes de la fecha límite.",
            ),
        )])], requires_external_rule_lookup=False, external_rule_lookup_status="resolved",
    )


def test_build_commercial_quote_uses_selected_option_and_air_rules():
    with patch("app.services.commercial_quote_builder.audit_stored_quote_live", return_value=_audit()):
        quote = build_commercial_quote(_record())
    assert quote.quote_id == "Q-TEST"
    assert quote.environment == "CERT"
    assert quote.trip_type == "round_trip"
    assert [(x.origin, x.destination) for x in quote.legs] == [("EZE","MIA"),("MIA","EZE")]
    option = quote.options[0]
    assert option.rank == 1
    assert option.commercial_labels == ["recommended"]
    fare = option.fares[0]
    assert fare.brand_name == "MAIN CABIN"
    assert fare.fare_basis_codes == ["QLN0AHM1"]
    assert fare.validating_carrier == "AA"
    assert fare.passenger_prices[1].passenger_type == "C10"
    assert fare.rules.changes == "Cambios permitidos con diferencia tarifaria."
    assert fare.rules.refunds == "Devolución no permitida."
    assert fare.rules.no_show == "No-show no permitido."


def test_build_commercial_quote_falls_back_when_air_rules_fails():
    with patch("app.services.commercial_quote_builder.audit_stored_quote_live", side_effect=RuntimeError("SOAP unavailable")):
        quote = build_commercial_quote(_record())
    fare = quote.options[0].fares[0]
    assert fare.rules.baggage == "1 pieza despachada de hasta 23 kg por pasajero."
    assert fare.rules.changes is None
    assert fare.rules.refunds is None
    assert fare.rules.no_show is None


def test_build_commercial_quote_requires_selection():
    record = _record().model_copy(update={"selected_ranks":[]})
    try:
        build_commercial_quote(record)
    except ValueError as exc:
        assert "no tiene opciones seleccionadas" in str(exc)
    else:
        raise AssertionError("Se esperaba ValueError")
