from __future__ import annotations

from app.config import get_settings
from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse, RankedOption, SabreSearchCall
from app.models.quote_request import Cabin, FarePreference, RequestProfile
from app.sabre.client import SabreClient
from app.sabre.shopping import SabreShoppingService, extract_bfm_diagnostics
from app.services.normalizer import merge_cabin_itineraries, merge_currency_itineraries, normalize_bfm_response
from app.services.pricing_rules import resolve_pricing_currencies_for_legs
from app.services.quote_renderer import render_ranked_client_quote
from app.services.ranking import rank_itineraries
from app.services.fare_preference_filter import filter_refundable_itineraries
from app.services.quote_repository import get_quote_repository


async def search_quote(request: QuoteSearchAPIRequest) -> QuoteSearchAPIResponse:
    search = request.to_search_request()
    settings = get_settings(request.environment)

    currencies = resolve_pricing_currencies_for_legs(
        search.effective_legs(), search.currency
    )
    if search.request_profile == RequestProfile.OFFICIAL:
        currencies = ["OFFICIAL"]

    cabins = request.effective_cabins
    if not request.cabins and request.business_companion:
        # Backward compatibility for older structured callers.
        cabins = [search.cabin]
        if search.cabin not in {Cabin.BUSINESS, Cabin.FIRST}:
            cabins.append(Cabin.BUSINESS)


    normalized_by_currency: dict[str, list] = {}
    calls: list[SabreSearchCall] = []

    async with SabreClient(settings) as client:
        shopping = SabreShoppingService(client, settings)
        for currency in currencies:
            override = None if currency == "OFFICIAL" else currency
            merged_for_currency: list = []

            for cabin in cabins:
                cabin_search = search.model_copy(update={"cabin": cabin})
                raw = await shopping.search(cabin_search, currency_override=override)
                diagnostics = extract_bfm_diagnostics(raw)
                cabin_options = normalize_bfm_response(raw)

                calls.append(
                    SabreSearchCall(
                        currency=currency,
                        cabin=cabin.value,
                        transaction_id=diagnostics.get("transaction_id"),
                        itinerary_count=diagnostics.get("itinerary_count", 0) or 0,
                        no_availability=bool(diagnostics.get("no_availability")),
                    )
                )

                if not merged_for_currency:
                    merged_for_currency = cabin_options
                else:
                    merged_for_currency = merge_cabin_itineraries(
                        merged_for_currency,
                        cabin_options,
                        cabins={cabin.value.lower()},
                        include_unmatched=True,
                    )

            normalized_by_currency[currency] = merged_for_currency

    keys = list(normalized_by_currency)
    normalized = normalized_by_currency[keys[0]] if keys else []
    if len(keys) == 2:
        normalized = merge_currency_itineraries(
            normalized_by_currency[keys[0]],
            normalized_by_currency[keys[1]],
        )

    if search.fare_preference == FarePreference.REFUNDABLE:
        normalized = filter_refundable_itineraries(normalized)

    ranking_currency = (
        "ARS" if normalized and normalized[0].is_domestic_argentina else "USD"
    )
    ranked = rank_itineraries(
        normalized,
        mode=request.sort,
        preferred_currency=ranking_currency,
    )[: search.max_options]

    response = QuoteSearchAPIResponse(
        environment=settings.sabre_env,
        effective_currencies=[key for key in keys if key != "OFFICIAL"],
        calls=calls,
        result_count=len(normalized),
        options=[
            RankedOption(
                rank=item.rank,
                score=item.score,
                stops=item.stops,
                duration_minutes=item.duration_minutes,
                ranking_currency=item.ranking_currency,
                ranking_price=item.ranking_price,
                itinerary=item.option,
            )
            for item in ranked
        ],
        client_quote=render_ranked_client_quote(ranked),
    )

    if request.persist:
        repository = get_quote_repository()
        quote_id = repository.create(request=request, response=response)
        response.quote_id = quote_id

    return response
