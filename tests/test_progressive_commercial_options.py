from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.models.api import (
    QuoteSearchAPIRequest,
    QuoteSearchAPIResponse,
    RankedOption,
    StoredQuoteRecord,
)
from app.models.itinerary import ItineraryOption
from app.services.commercial_quote_builder import build_commercial_quote
from app.services.quote_repository import QuoteRepository


ROOT = Path(__file__).resolve().parents[1]


def itinerary(rank: int) -> ItineraryOption:
    return ItineraryOption.model_validate(
        {
            "segments": [
                {
                    "marketing_carrier": "AA",
                    "flight_number": str(900 + rank),
                    "departure_airport": "EZE",
                    "arrival_airport": "MIA",
                    "departure_at": f"2026-09-19T{10 + (rank % 8):02d}:00:00",
                    "arrival_at": f"2026-09-19T{18 + (rank % 5):02d}:00:00",
                }
            ],
            "fare": {
                "cabin": "economy",
                "currency": "USD",
                "price_per_passenger": str(100 + rank),
                "fare_basis_codes": [f"Y{rank}"],
                "validating_carrier": "AA",
                "brand_name": "MAIN CABIN",
            },
            "fares_by_currency": {},
            "fare_options_by_currency": {},
        }
    )


def ranked(rank: int) -> RankedOption:
    return RankedOption(
        rank=rank,
        score=Decimal(str(100 - rank)),
        stops=0,
        duration_minutes=480,
        ranking_currency="USD",
        ranking_price=Decimal(str(100 + rank)),
        itinerary=itinerary(rank),
    )


def response_with_candidates() -> QuoteSearchAPIResponse:
    return QuoteSearchAPIResponse(
        environment="CERT",
        effective_currencies=["USD"],
        calls=[],
        result_count=10,
        available_option_count=10,
        options=[ranked(i) for i in range(1, 6)],
        candidate_options=[ranked(i) for i in range(6, 11)],
        client_quote="TEST",
    )


def request() -> QuoteSearchAPIRequest:
    return QuoteSearchAPIRequest(
        environment="cert",
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        adults=1,
        persist=True,
    )


def test_candidate_options_are_not_serialized_to_client():
    response = response_with_candidates()
    payload = response.model_dump(mode="json")

    assert "candidate_options" not in payload
    assert payload["available_option_count"] == 10
    assert len(payload["options"]) == 5


def test_repository_persists_candidates_server_side_and_allows_selection(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = repo.create(
        request=request(),
        response=response_with_candidates(),
    )

    record = repo.get(quote_id)
    assert record is not None
    assert len(record.quote_response["options"]) == 5
    assert len(record.quote_response["_candidate_options"]) == 5

    selection = repo.select(quote_id, [6, 10])
    assert selection.selected_ranks == [6, 10]


def test_commercial_builder_pages_across_visible_and_candidate_options():
    response = response_with_candidates()
    quote_response = response.model_dump(mode="json")
    quote_response["_candidate_options"] = [
        item.model_dump(mode="json")
        for item in response.candidate_options
    ]
    record = StoredQuoteRecord(
        quote_id="Q-PAGE",
        created_at="2026-08-21T12:00:00+00:00",
        updated_at="2026-08-21T12:00:00+00:00",
        status="active",
        source="agent",
        search_request=request().model_dump(mode="json"),
        quote_response=quote_response,
    )

    with patch(
        "app.services.commercial_quote_builder.audit_stored_quote_live",
        side_effect=RuntimeError("skip SOAP"),
    ):
        first = build_commercial_quote(
            record,
            selected_only=False,
            offset=0,
            limit=5,
        )
        first_ten = build_commercial_quote(
            record,
            selected_only=False,
            offset=0,
            limit=10,
        )

    assert [item.rank for item in first.options] == [1, 2, 3, 4, 5]
    assert [item.rank for item in first_ten.options] == list(range(1, 11))


def test_workspace_loads_five_then_exposes_ver_cinco_mas():
    html = (ROOT / "app" / "web" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "const commercialPageSize = 5;" in html
    assert 'id="moreOptions"' in html
    assert "async function loadMoreCommercialOptions()" in html
    assert "Ver ${next} más" in html
    assert "currentCommercialVisibleLimit+commercialPageSize" in html
    assert "available_option_count" in html
    assert "limit=${currentCommercialVisibleLimit}" in html
