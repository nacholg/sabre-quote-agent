from __future__ import annotations

from dataclasses import replace

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


def _matches_preferred_carriers(option, carriers: list[str]) -> bool:
    allowed = {code.upper() for code in carriers}
    return bool(option.segments) and all(
        segment.marketing_carrier.upper() in allowed
        for segment in option.segments
    )


def _apply_excluded_carriers(options: list, excluded_carriers: list[str]) -> list:
    if not excluded_carriers:
        return options

    excluded = {code.upper() for code in excluded_carriers}
    return [
        option
        for option in options
        if all(
            segment.marketing_carrier.upper() not in excluded
            for segment in option.segments
        )
    ]


def _diversify_ranked_by_carrier(ranked: list, limit: int) -> list:
    if not ranked or limit <= 0:
        return []

    chosen = []
    chosen_ids: set[int] = set()
    seen_carriers: set[str] = set()

    for item in ranked:
        carrier = (
            item.option.segments[0].marketing_carrier.upper()
            if item.option.segments
            else ""
        )
        if carrier and carrier not in seen_carriers:
            chosen.append(item)
            chosen_ids.add(id(item))
            seen_carriers.add(carrier)
            if len(chosen) >= limit:
                break

    if len(chosen) < limit:
        for item in ranked:
            if id(item) in chosen_ids:
                continue
            chosen.append(item)
            if len(chosen) >= limit:
                break

    return [
        replace(item, rank=index)
        for index, item in enumerate(chosen, start=1)
    ]


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

                raw = await shopping.search(
                    cabin_search,
                    currency_override=override,
                )
                diagnostics = extract_bfm_diagnostics(raw)
                normalized_primary = normalize_bfm_response(raw)

                cabin_options = _apply_excluded_carriers(
                    normalized_primary,
                    search.excluded_carriers,
                )

                if search.preferred_carriers:
                    cabin_options = [
                        option
                        for option in cabin_options
                        if _matches_preferred_carriers(
                            option,
                            search.preferred_carriers,
                        )
                    ]

                calls.append(
                    SabreSearchCall(
                        currency=currency,
                        cabin=cabin.value,
                        mode="primary",
                        preferred_carriers=list(search.preferred_carriers),
                        transaction_id=diagnostics.get("transaction_id"),
                        itinerary_count=diagnostics.get("itinerary_count", 0) or 0,
                        normalized_count=len(normalized_primary),
                        post_filter_count=len(cabin_options),
                        no_availability=bool(diagnostics.get("no_availability")),
                    )
                )

                if search.preferred_carriers and not cabin_options:
                    broad_search = cabin_search.model_copy(
                        update={"preferred_carriers": []}
                    )

                    broad_raw = await shopping.search(
                        broad_search,
                        currency_override=override,
                    )
                    broad_diagnostics = extract_bfm_diagnostics(broad_raw)
                    broad_normalized = normalize_bfm_response(broad_raw)

                    broad_filtered = _apply_excluded_carriers(
                        broad_normalized,
                        search.excluded_carriers,
                    )

                    cabin_options = [
                        option
                        for option in broad_filtered
                        if _matches_preferred_carriers(
                            option,
                            search.preferred_carriers,
                        )
                    ]

                    calls.append(
                        SabreSearchCall(
                            currency=currency,
                            cabin=cabin.value,
                            mode="carrier_fallback",
                            preferred_carriers=list(search.preferred_carriers),
                            transaction_id=broad_diagnostics.get("transaction_id"),
                            itinerary_count=broad_diagnostics.get("itinerary_count", 0) or 0,
                            normalized_count=len(broad_normalized),
                            post_filter_count=len(cabin_options),
                            no_availability=bool(
                                broad_diagnostics.get("no_availability")
                            ),
                            fallback_used=True,
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
        "ARS"
        if normalized and normalized[0].is_domestic_argentina
        else "USD"
    )

    ranked_all = rank_itineraries(
        normalized,
        mode=request.sort,
        preferred_currency=ranking_currency,
    )

    if search.preferred_carriers:
        ranked = ranked_all[: search.max_options]
    else:
        ranked = _diversify_ranked_by_carrier(
            ranked_all,
            search.max_options,
        )

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
