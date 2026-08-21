from __future__ import annotations

from decimal import Decimal

from app.models.api import (
    FarePriceChange,
    QuoteRefreshResponse,
    QuoteSearchAPIRequest,
    RefreshedOptionComparison,
)
from app.models.itinerary import ItineraryOption
from app.services.normalizer import itinerary_signature
from app.services.quote_repository import QuoteRepository
from app.services.quote_service import search_quote


def _fare_map(option: ItineraryOption) -> dict[tuple[str, str, str], Decimal]:
    result = {}
    for currency, fares in (option.fare_options_by_currency or {}).items():
        for fare in fares:
            key = (
                fare.cabin.lower(),
                (fare.brand_name or fare.brand_code or "").upper(),
                currency,
            )
            result[key] = fare.price_per_passenger
    if not result:
        fare = option.fare
        result[(fare.cabin.lower(), (fare.brand_name or fare.brand_code or "").upper(), fare.currency)] = fare.price_per_passenger
    return result


async def refresh_stored_quote(repo: QuoteRepository, quote_id: str) -> QuoteRefreshResponse:
    record = repo.assert_latest(quote_id)

    request = QuoteSearchAPIRequest.model_validate(record.search_request)
    request.persist = False
    fresh = await search_quote(request)

    new_id = repo.create(
        request=request,
        response=fresh,
        source="refresh",
        agent_text=record.agent_text,
        interpretation=record.interpretation,
        parent_quote_id=quote_id,
    )
    repo.update_workflow(
        new_id,
        client_name=record.client_name,
        client_reference=record.client_reference,
        notes=record.notes,
    )
    repo.link_refresh(quote_id, new_id)

    fresh_by_sig = {
        itinerary_signature(item.itinerary): item
        for item in (
            list(fresh.options)
            + list(fresh.candidate_options)
        )
    }
    comparisons = []
    selected = set(record.selected_ranks or [])
    old_items = list(
        record.quote_response.get("options") or []
    ) + list(
        record.quote_response.get("_candidate_options") or []
    )
    for old_item in old_items:
        old_rank = int(old_item["rank"])
        if selected and old_rank not in selected:
            continue
        old_option = ItineraryOption.model_validate(old_item["itinerary"])
        match = fresh_by_sig.get(itinerary_signature(old_option))
        if match is None:
            comparisons.append(
                RefreshedOptionComparison(
                    old_rank=old_rank,
                    itinerary_status="unavailable",
                    new_rank=None,
                    fare_changes=[],
                )
            )
            continue

        old_fares = _fare_map(old_option)
        new_fares = _fare_map(match.itinerary)
        changes = []
        for key, old_price in old_fares.items():
            new_price = new_fares.get(key)
            cabin, brand, currency = key
            if new_price is None:
                status = "unavailable"
                delta = None
            else:
                delta = new_price - old_price
                status = "same" if delta == 0 else "changed"
            changes.append(
                FarePriceChange(
                    cabin=cabin,
                    brand_name=brand or None,
                    currency=currency,
                    old_price=old_price,
                    new_price=new_price,
                    delta=delta,
                    status=status,
                )
            )
        comparisons.append(
            RefreshedOptionComparison(
                old_rank=old_rank,
                new_rank=match.rank,
                itinerary_status="same",
                fare_changes=changes,
            )
        )

    return QuoteRefreshResponse(
        original_quote_id=quote_id,
        refreshed_quote_id=new_id,
        comparisons=comparisons,
    )
