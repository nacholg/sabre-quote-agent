from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from app.models.api import AgentQuoteRequest
from app.services.agent_parser import parse_agent_quote


TODAY = date(2026, 8, 14)


def test_parse_natural_quote_direct_excluding_ar():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "Cotizame EZE-MIA del 19 al 30 de septiembre, 1 adulto, "
                "solo directos, cualquier aerolínea excepto AR, en USD"
            ),
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.origin == "EZE"
    assert req.destination == "MIA"
    assert str(req.departure_date) == "2026-09-19"
    assert str(req.return_date) == "2026-09-30"
    assert req.direct is True
    assert req.carriers == []
    assert req.excluded_carriers == ["AR"]
    assert req.currency.value == "USD"


def test_parse_named_carriers_and_baggage():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "Buscame EZE a MIA del 19 al 30 de septiembre con AA, AR y LATAM, "
                "con valija, 2 adultos, pasame 8 opciones"
            ),
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.carriers == ["AA", "AR", "LA"]
    assert req.fare_preference.value == "baggage"
    assert req.adults == 2
    assert req.max_options == 8


def test_parse_currency_both():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="EZE MIA 19/09/2026 al 30/09/2026, USD y ARS",
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.currency.value == "BOTH"


def test_agent_endpoint_interpret_only():
    with TestClient(app) as client:
        response = client.post(
            "/agent/quote",
            json={
                "text": (
                    "Cotizame EZE-MIA del 19 al 30 de septiembre, "
                    "directo, AA o LATAM, exclui AR, USD"
                ),
                "environment": "cert",
                "execute": False
            },
        )
    assert response.status_code == 200
    body = response.json()
    req = body["interpretation"]["search_request"]
    assert req["origin"] == "EZE"
    assert req["destination"] == "MIA"
    assert req["direct"] is True
    assert req["carriers"] == ["AA", "LA"]
    assert req["excluded_carriers"] == ["AR"]
    assert body["quote"] is None


def test_agent_endpoint_requires_route():
    with TestClient(app) as client:
        response = client.post(
            "/agent/quote",
            json={"text": "Cotizame algo barato para septiembre", "execute": False},
        )
    assert response.status_code == 422


def test_direct_interpretation_sets_max_stops_zero():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE-MIA 19 al 30 de septiembre, directo",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.direct is True
    assert req.max_stops == 0


def test_domestic_argentina_forces_ars_from_usd():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame COR-AEP del 19 al 30 de septiembre, directo, AR, USD",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.currency.value == "ARS"
    assert any("domésticos dentro de Argentina" in warning for warning in parsed.warnings)


def test_domestic_argentina_ars_rule_is_explained():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame COR-AEP del 19 al 30 de septiembre, directo, AR, ARS",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.currency.value == "ARS"
    assert any("moneda ARS obligatoria" in item for item in parsed.assumptions)


def test_natural_people_buenos_aires_domestic_and_baggage():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Buscame para dos personas Córdoba Buenos Aires del 19 al 30 de septiembre, directo por Aerolíneas, con valija",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.origin == "COR"
    assert req.destination == "AEP"
    assert req.adults == 2
    assert req.direct is True
    assert req.carriers == ["AR"]
    assert req.currency.value == "ARS"
    assert req.fare_preference.value == "baggage"
    assert any("Buenos Aires" in item for item in parsed.assumptions)


def test_buenos_aires_resolves_eze_for_international():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Buenos Aires Miami del 19 al 30 de septiembre, 1 adulto, directo",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.origin == "EZE"
    assert req.destination == "MIA"


def test_number_words_and_child_age():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="EZE MIA del 19 al 30 de septiembre, dos adultos y un niño de 7 años",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.adults == 2
    assert req.children == 1
    assert req.child_age == 7


def test_any_airline_less_ar_natural():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="EZE MIA del 19 al 30 de septiembre, cualquier compañía menos Aerolíneas, vuelos directos",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.carriers == []
    assert req.excluded_carriers == ["AR"]
    assert req.direct is True


def test_domestic_auto_currency_does_not_warn_as_override():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Córdoba Buenos Aires del 19 al 30 de septiembre, directo",
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.currency.value == "ARS"
    assert not any("Se solicitó otra moneda" in warning for warning in parsed.warnings)


def test_multiple_child_ages_are_preserved():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="EZE MIA del 19 al 30 de septiembre, 2 adultos, un niño de 9 y otro de 4",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    children = [p for p in req.passengers if p.type.value == "CHILD"]
    assert [(p.age, p.quantity) for p in children] == [(9, 1), (4, 1)]


def test_child_12_is_treated_as_adult():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="EZE MIA del 19 al 30 de septiembre, 2 adultos y un niño de 12",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    adults = [p for p in req.passengers if p.type.value == "ADT"]
    children = [p for p in req.passengers if p.type.value == "CHILD"]
    assert adults[0].quantity == 3
    assert children == []
    assert any("tratado como ADT" in warning for warning in parsed.warnings)


def test_child_without_age_requires_input():
    with TestClient(app) as client:
        response = client.post(
            "/agent/quote",
            json={
                "text": "EZE MIA del 19 al 30 de septiembre, 2 adultos y 2 niños",
                "execute": False,
            },
        )
    assert response.status_code == 422
    assert "edad de cada menor" in response.json()["detail"]


def test_exclude_latam_with_no_cotizar():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame AEP-GRU del 19 al 30 de septiembre, 1 adulto, directo, USD, branded, no cotizar latam",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.carriers == []
    assert req.excluded_carriers == ["LA"]


def test_only_gol_linhas_aereas_maps_to_g3():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame AEP-GRU del 19 al 30 de septiembre, 1 adulto, directo, USD, branded, solo con Gol Linhas Aereas (G3)",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.carriers == ["G3"]
    assert req.excluded_carriers == []


def test_with_g3_maps_to_g3():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame AEP-GRU del 19 al 30 de septiembre, 1 adulto, directo, USD, branded, con G3",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.carriers == ["G3"]
    assert req.excluded_carriers == []


def test_explicit_uppercase_la_still_maps_to_latam():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame AEP-GRU del 19 al 30 de septiembre, 1 adulto, directo, con LA",
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.carriers == ["LA"]


def test_reference_db_resolves_turkish_airlines():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE-MAD del 19 al 30 de septiembre, con Turkish",
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.carriers == ["TK"]


def test_reference_db_resolves_air_europa():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE-MAD del 19 al 30 de septiembre, solo Air Europa",
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.carriers == ["UX"]


def test_reference_db_resolves_san_pablo_city_code():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame AEP a San Pablo del 19 al 30 de septiembre, 1 adulto",
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.origin == "AEP"
    assert parsed.search_request.destination == "SAO"


def test_large_reference_catalog_does_not_turn_spanish_words_into_airlines():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE a Ljubljana del 19 al 30 de septiembre, USD",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.origin == "EZE"
    assert req.destination == "LJU"
    assert req.carriers == []
    assert req.excluded_carriers == []


def test_lowercase_common_words_are_not_airport_codes():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE a Ljubljana del 19 al 30 de septiembre, USD",
            execute=False,
        ),
        today=TODAY,
    )
    assert not any("más de dos aeropuertos" in w for w in parsed.warnings)


def test_explicit_uppercase_airline_code_still_works_with_large_catalog():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE a Madrid del 19 al 30 de septiembre, con TK",
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.carriers == ["TK"]


def test_refundable_phrase_becomes_strict_refundable_preference():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE a Ljubljana del 19 al 30 de septiembre, USD, en premium economy, con devolucion",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.cabin.value == "PREMIUM_ECONOMY"
    assert [c.value for c in req.cabins] == ["PREMIUM_ECONOMY"]
    assert req.fare_preference.value == "refundable"
    assert req.business_companion is False


def test_no_cabin_means_three_commercial_cabins():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE a Ljubljana del 19 al 30 de septiembre, USD, con devolucion",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert [c.value for c in req.cabins] == [
        "ECONOMY", "PREMIUM_ECONOMY", "BUSINESS"
    ]
    assert any("No se indicó cabina" in item for item in parsed.assumptions)


def test_explicit_multiple_cabins_are_preserved():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE a Ljubljana del 19 al 30 de septiembre, en economy, premium economy y business, USD, con devolucion",
            execute=False,
        ),
        today=TODAY,
    )
    assert [c.value for c in parsed.search_request.cabins] == [
        "ECONOMY", "PREMIUM_ECONOMY", "BUSINESS"
    ]


def test_mixed_outbound_return_cabins_are_detected_and_not_silently_flattened():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE a Ljubljana del 19 al 30 de septiembre, USD, ida business, vuelta premium economy, con devolucion",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.outbound_cabin.value == "BUSINESS"
    assert req.return_cabin.value == "PREMIUM_ECONOMY"
    assert req.has_mixed_leg_cabins is True
    assert any("cabinas distintas por tramo" in warning for warning in parsed.warnings)


def test_lowercase_iata_route_codes_are_recognized():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="cotizar eze mia para el 20 de octubre con regreso el 30 de octubre, economy, con devolucion, directo, usd",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.origin == "EZE"
    assert req.destination == "MIA"


def test_lowercase_iata_with_hyphen_is_recognized():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="cotizar mad-mex para el 20 de septiembre hasta el 28, economy con devolucion",
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.origin == "MAD"
    assert req.destination == "MEX"


def test_common_words_do_not_become_location_codes():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text="Cotizame EZE a Ljubljana del 19 al 30 de septiembre, USD",
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.origin == "EZE"
    assert parsed.search_request.destination == "LJU"
    assert not any("más de dos aeropuertos" in w for w in parsed.warnings)


def test_iberia_is_airline_not_location():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "cotizar eze mad para el 20 de diciembre con regreso el 30 de diciembre, "
                "en economy, solo Iberia, usd"
            ),
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.origin == "EZE"
    assert req.destination == "MAD"
    assert req.carriers == ["IB"]
    assert req.excluded_carriers == []
    assert req.currency.value == "USD"
    assert [c.value for c in req.cabins] == ["ECONOMY"]
    assert not any("más de dos aeropuertos" in warning for warning in parsed.warnings)


def test_iberia_or_aerolineas_maps_to_both_carriers():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "Cotizame EZE-MAD del 20 al 30 de diciembre, "
                "Iberia o Aerolíneas, economy, USD"
            ),
            execute=False,
        ),
        today=TODAY,
    )
    assert parsed.search_request.carriers == ["AR", "IB"]


def test_any_airline_except_iberia_excludes_ib():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "Cotizame EZE-MAD del 20 al 30 de diciembre, "
                "cualquier aerolínea excepto Iberia, economy, USD"
            ),
            execute=False,
        ),
        today=TODAY,
    )
    req = parsed.search_request
    assert req.carriers == []
    assert req.excluded_carriers == ["IB"]
