from app.models.api import StoredQuoteRecord
from app.sabre.air_rules import (
    AirRulesCategory,
    AirRulesParsedResponse,
)
from app.sabre.soap_client import SoapResult
from app.sabre.soap_session import SoapSession
from app.services.air_rules_service import AirRulesLookupResult
from app.services.live_air_rules_audit import (
    LiveAirRulesAuditor,
    collect_rule_queries,
)


def record() -> StoredQuoteRecord:
    return StoredQuoteRecord(
        quote_id="Q-LIVE",
        created_at="2026-08-18T00:00:00",
        updated_at="2026-08-18T00:00:00",
        status="selected",
        selected_ranks=[1],
        source="agent",
        search_request={
            "environment": "cert",
            "origin": "EZE",
            "destination": "JFK",
            "departure_date": "2027-01-20",
        },
        quote_response={
            "options": [
                {
                    "rank": 1,
                    "itinerary": {
                        "segments": [
                            {
                                "marketing_carrier": "AA",
                                "flight_number": "954",
                                "departure_airport": "EZE",
                                "arrival_airport": "JFK",
                                "departure_at": "2027-01-20T21:10:00-03:00",
                                "arrival_at": "2027-01-21T06:00:00-05:00",
                            }
                        ],
                        "fare": {
                            "cabin": "economy",
                            "currency": "USD",
                            "price_per_passenger": "916.23",
                            "fare_basis_codes": ["LLX5ABM1"],
                        },
                        "fares_by_currency": {
                            "USD": {
                                "cabin": "economy",
                                "currency": "USD",
                                "price_per_passenger": "916.23",
                                "fare_basis_codes": ["LLX5ABM1"],
                            }
                        },
                    },
                }
            ]
        },
    )


class FakeSessionService:
    def create(self):
        return SoapSession(
            binary_security_token="TOKEN",
            conversation_id="CONV",
            transport=SoapResult(
                status_code=200,
                text="",
                content_type="text/xml",
                url="https://example.test",
            ),
        )


class FakeRulesService:
    def lookup(self, request):
        parsed = AirRulesParsedResponse(
            success=True,
            categories=(
                AirRulesCategory(
                    number=16,
                    title="PENALTIES",
                    text=(
                        "TICKET IS NON-REFUNDABLE IN CASE OF CANCEL/REFUND. "
                        "CHANGES PERMITTED."
                    ),
                ),
            ),
        )
        return AirRulesLookupResult(
            request=request,
            transport=SoapResult(
                status_code=200,
                text="<ok/>",
                content_type="text/xml",
                url="https://example.test",
            ),
            parsed=parsed,
        )


class BrokenSessionService:
    def create(self):
        raise RuntimeError("SOAP unavailable")


def test_collect_rule_queries_from_selected_fare():
    queries = collect_rule_queries(record())

    assert len(queries) == 1
    query = queries[0]
    assert query.origin == "EZE"
    assert query.destination == "JFK"
    assert query.carrier == "AA"
    assert query.fare_basis == "LLX5ABM1"
    assert str(query.departure_date) == "2027-01-20"


def test_live_auditor_enriches_category_16():
    result = LiveAirRulesAuditor(
        session_service=FakeSessionService(),
        air_rules_service=FakeRulesService(),
        pcc="RY3A",
    ).audit(record())

    fare = result.options[0].fares[0]
    assert fare.changes.source == "air_rules"
    assert fare.changes.status == "allowed"
    assert fare.refunds.source == "air_rules"
    assert fare.refunds.status == "not_allowed"
    assert result.external_rule_lookup_status == "resolved"


def test_live_auditor_falls_back_when_session_fails():
    result = LiveAirRulesAuditor(
        session_service=BrokenSessionService(),
        air_rules_service=FakeRulesService(),
        pcc="RY3A",
    ).audit(record())

    assert result.external_rule_lookup_status == "lookup_failed"
    assert result.options[0].fares[0].changes.source != "air_rules"
