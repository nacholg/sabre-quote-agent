from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.config import get_settings
from app.models.api import FareRuleAuditResponse, StoredQuoteRecord
from app.models.itinerary import ItineraryOption
from app.sabre.air_rules import AirRulesParsedResponse, AirRulesRequest
from app.sabre.soap_client import SabreSoapClient
from app.sabre.soap_session import SabreSoapSessionService, SoapSession
from app.services.air_rules_service import AirRulesService
from app.services.fare_rule_reliability import (
    audit_stored_quote,
    commercial_fares,
)


@dataclass(frozen=True)
class RuleLookupQuery:
    origin: str
    destination: str
    departure_date: date
    carrier: str
    fare_basis: str


def _selected_raw_options(
    record: StoredQuoteRecord,
    *,
    selected_only: bool,
) -> list[dict]:
    raw = list(
        record.quote_response.get("options") or []
    ) + list(
        record.quote_response.get("_candidate_options") or []
    )
    if selected_only and record.selected_ranks:
        wanted = set(record.selected_ranks)
        raw = [
            item
            for item in raw
            if int(item.get("rank", 0)) in wanted
        ]
    return raw


def collect_rule_queries(
    record: StoredQuoteRecord,
    *,
    selected_only: bool = True,
) -> list[RuleLookupQuery]:
    queries: list[RuleLookupQuery] = []
    seen: set[str] = set()

    search = record.search_request or {}

    for item in _selected_raw_options(
        record,
        selected_only=selected_only,
    ):
        option = ItineraryOption.model_validate(item["itinerary"])
        if not option.segments:
            continue

        first_segment = option.segments[0]

        origin = (
            str(search.get("origin") or "").upper()
            or first_segment.departure_airport.upper()
        )
        destination = (
            str(search.get("destination") or "").upper()
            or option.segments[-1].arrival_airport.upper()
        )

        departure_raw = search.get("departure_date")
        departure_date = (
            date.fromisoformat(str(departure_raw))
            if departure_raw
            else first_segment.departure_at.date()
        )

        carrier = first_segment.marketing_carrier.upper()

        for fare in commercial_fares(option):
            for fare_basis in fare.fare_basis_codes:
                code = fare_basis.strip().upper()
                if not code or code in seen:
                    continue
                seen.add(code)
                queries.append(
                    RuleLookupQuery(
                        origin=origin,
                        destination=destination,
                        departure_date=departure_date,
                        carrier=carrier,
                        fare_basis=code,
                    )
                )

    return queries


class LiveAirRulesAuditor:
    def __init__(
        self,
        *,
        session_service,
        air_rules_service,
        pcc: str,
    ):
        self.session_service = session_service
        self.air_rules_service = air_rules_service
        self.pcc = pcc

    def audit(
        self,
        record: StoredQuoteRecord,
        *,
        selected_only: bool = True,
    ) -> FareRuleAuditResponse:
        baseline = audit_stored_quote(
            record,
            selected_only=selected_only,
        )
        queries = collect_rule_queries(
            record,
            selected_only=selected_only,
        )

        if not queries:
            return baseline

        try:
            session: SoapSession = self.session_service.create()
        except Exception:
            return baseline.model_copy(
                update={
                    "external_rule_lookup_status": "lookup_failed",
                }
            )

        parsed_by_fare_basis: dict[str, AirRulesParsedResponse] = {}
        attempted = 0

        for query in queries:
            attempted += 1
            try:
                result = self.air_rules_service.lookup(
                    AirRulesRequest(
                        pcc=self.pcc,
                        conversation_id=session.conversation_id,
                        binary_security_token=session.binary_security_token,
                        origin=query.origin,
                        destination=query.destination,
                        departure_date=query.departure_date,
                        carrier=query.carrier,
                        fare_basis=query.fare_basis,
                        category=16,
                    )
                )
            except Exception:
                continue

            if result.ok and result.parsed.categories:
                parsed_by_fare_basis[query.fare_basis] = result.parsed

        if not parsed_by_fare_basis:
            return baseline.model_copy(
                update={
                    "external_rule_lookup_status": "lookup_failed",
                }
            )

        enriched = audit_stored_quote(
            record,
            selected_only=selected_only,
            air_rules_by_fare_basis=parsed_by_fare_basis,
        )

        if enriched.requires_external_rule_lookup:
            status = "partial"
        else:
            status = "resolved"

        return enriched.model_copy(
            update={
                "external_rule_lookup_status": status,
            }
        )


def audit_stored_quote_live(
    record: StoredQuoteRecord,
    *,
    selected_only: bool = True,
) -> FareRuleAuditResponse:
    env = str(
        (record.search_request or {}).get("environment")
        or "cert"
    ).lower()
    if env not in {"cert", "prod"}:
        env = "cert"

    settings = get_settings(env)
    soap_client = SabreSoapClient(
        settings.soap_endpoint,
        timeout=settings.sabre_timeout_seconds,
    )
    session_service = SabreSoapSessionService(
        settings,
        client=soap_client,
    )
    air_rules_service = AirRulesService(soap_client)

    return LiveAirRulesAuditor(
        session_service=session_service,
        air_rules_service=air_rules_service,
        pcc=settings.sabre_pcc,
    ).audit(
        record,
        selected_only=selected_only,
    )
