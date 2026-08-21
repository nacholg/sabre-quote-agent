from __future__ import annotations

import asyncio
from dataclasses import replace
from time import perf_counter

from app.config import get_settings
from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse, RankedOption, SabreSearchCall
from app.models.quote_request import Cabin, FarePreference, RequestProfile
from app.sabre.client import SabreClient
from app.sabre.shopping import SabreShoppingService, extract_bfm_diagnostics
from app.services.normalizer import merge_cabin_itineraries, merge_currency_itineraries, normalize_bfm_response
from app.services.pricing_rules import resolve_pricing_currencies_for_legs
from app.services.quote_renderer import render_ranked_client_quote
from app.services.ranking import (
    RankingMode,
    assign_commercial_labels,
    commercial_rank_itineraries,
    rank_itineraries,
)
from app.services.fare_preference_filter import filter_refundable_itineraries
from app.services.quote_repository import get_quote_repository
from app.services.time_constraint_filter import (
    apply_time_constraints,
    reorder_ranked_by_time,
)



_CABIN_TO_FARE_NAME = {
    Cabin.ECONOMY: "economy",
    Cabin.PREMIUM_ECONOMY: "premium economy",
    Cabin.BUSINESS: "business",
    Cabin.FIRST: "first",
}

_CABIN_TO_SABRE_CODE = {
    Cabin.ECONOMY: "Y",
    Cabin.PREMIUM_ECONOMY: "S",
    Cabin.BUSINESS: "C",
    Cabin.FIRST: "F",
}


def _fare_matches_requested_cabin(fare, cabin: Cabin) -> bool:
    # Structured component cabin codes are authoritative when Sabre supplies them.
    # A mixed pricing row is accepted only if all its components belong to the
    # requested cabin. Brand names never participate in this classification.
    expected_code = _CABIN_TO_SABRE_CODE[cabin]
    structured_codes = {
        code.upper()
        for code in getattr(fare, "cabin_codes", [])
        if code
    }

    if structured_codes:
        return structured_codes == {expected_code}

    return fare.cabin.strip().lower() == _CABIN_TO_FARE_NAME[cabin]


def _filter_itineraries_to_cabin(options: list, cabin: Cabin) -> list:
    filtered_options = []

    for option in options:
        copy = option.model_copy(deep=True)
        filtered_by_currency = {}

        for currency, fares in copy.fare_options_by_currency.items():
            kept = [
                fare
                for fare in fares
                if _fare_matches_requested_cabin(fare, cabin)
            ]
            if kept:
                filtered_by_currency[currency] = kept

        if not filtered_by_currency:
            fallback_fares = {
                copy.fare.currency: [copy.fare]
            }
            kept = [
                fare
                for fare in fallback_fares[copy.fare.currency]
                if _fare_matches_requested_cabin(fare, cabin)
            ]
            if kept:
                filtered_by_currency[copy.fare.currency] = kept

        if not filtered_by_currency:
            continue

        copy.fare_options_by_currency = filtered_by_currency
        copy.fares_by_currency = {
            currency: fares[0]
            for currency, fares in filtered_by_currency.items()
        }

        primary_currency = copy.fare.currency
        if primary_currency in filtered_by_currency:
            copy.fare = filtered_by_currency[primary_currency][0]
        else:
            first_currency = next(iter(filtered_by_currency))
            copy.fare = filtered_by_currency[first_currency][0]

        filtered_options.append(copy)

    return filtered_options


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


async def _timed_primary_bfm_search(
    shopping,
    cabin_search,
    *,
    currency_override,
    semaphore,
):
    async with semaphore:
        started = perf_counter()
        raw = await shopping.search(
            cabin_search,
            currency_override=currency_override,
        )
        return raw, perf_counter() - started


async def search_quote(request: QuoteSearchAPIRequest) -> QuoteSearchAPIResponse:
    _quote_started = perf_counter()
    _bfm_seconds = 0.0
    _bfm_wall_seconds = 0.0
    _normalize_seconds = 0.0
    _persist_seconds = 0.0
    _bfm_calls = 0
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

            _cabin_semaphore = asyncio.Semaphore(3)
            _parallel_started = perf_counter()
            _primary_results = await asyncio.gather(
                *[
                    _timed_primary_bfm_search(
                        shopping,
                        search.model_copy(update={"cabin": cabin}),
                        currency_override=override,
                        semaphore=_cabin_semaphore,
                    )
                    for cabin in cabins
                ]
            )
            _parallel_elapsed = perf_counter() - _parallel_started
            _bfm_wall_seconds += _parallel_elapsed
            print(
                f"[QUOTE] BFM cabin batch currency={currency}: "
                f"{_parallel_elapsed:.3f}s wall | "
                f"{len(cabins)} cabin calls"
            )

            _primary_by_cabin = {
                cabin: result
                for cabin, result in zip(cabins, _primary_results)
            }

            for cabin in cabins:
                cabin_search = search.model_copy(update={"cabin": cabin})
                raw, _bfm_primary_elapsed = _primary_by_cabin[cabin]
                _bfm_seconds += _bfm_primary_elapsed
                _bfm_calls += 1
                print(
                    f"[QUOTE] BFM #{_bfm_calls} "
                    f"currency={currency} cabin={cabin.value}: "
                    f"{_bfm_primary_elapsed:.3f}s service"
                )

                diagnostics = extract_bfm_diagnostics(raw)

                _normalize_started = perf_counter()
                normalized_primary = normalize_bfm_response(raw)
                _normalize_elapsed = perf_counter() - _normalize_started
                _normalize_seconds += _normalize_elapsed
                print(
                    f"[QUOTE] normalize #{_bfm_calls}: "
                    f"{_normalize_elapsed:.3f}s"
                )
                cabin_normalized = _filter_itineraries_to_cabin(
                    normalized_primary,
                    cabin,
                )

                cabin_options = _apply_excluded_carriers(
                    cabin_normalized,
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

                    _bfm_fallback_started = perf_counter()
                    broad_raw = await shopping.search(
                        broad_search,
                        currency_override=override,
                    )
                    _bfm_fallback_elapsed = perf_counter() - _bfm_fallback_started
                    _bfm_wall_seconds += _bfm_fallback_elapsed
                    _bfm_seconds += _bfm_fallback_elapsed
                    _bfm_calls += 1
                    print(
                        f"[QUOTE] BFM fallback #{_bfm_calls} "
                        f"currency={currency} cabin={cabin.value}: "
                        f"{_bfm_fallback_elapsed:.3f}s"
                    )

                    broad_diagnostics = extract_bfm_diagnostics(broad_raw)

                    _normalize_started = perf_counter()
                    broad_normalized = normalize_bfm_response(broad_raw)
                    _normalize_elapsed = perf_counter() - _normalize_started
                    _normalize_seconds += _normalize_elapsed
                    print(
                        f"[QUOTE] normalize fallback #{_bfm_calls}: "
                        f"{_normalize_elapsed:.3f}s"
                    )
                    broad_cabin_normalized = _filter_itineraries_to_cabin(
                        broad_normalized,
                        cabin,
                    )

                    broad_filtered = _apply_excluded_carriers(
                        broad_cabin_normalized,
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

    time_filter = apply_time_constraints(
        normalized,
        search.effective_legs(),
        search.time_constraints,
    )
    normalized = time_filter.options

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

    if request.sort == RankingMode.BALANCED:
        ranked_all = commercial_rank_itineraries(
            ranked_all,
            time_distance_by_signature=time_filter.distance_by_signature,
            has_time_constraints=bool(search.time_constraints),
        )

    ranked_all = reorder_ranked_by_time(
        ranked_all,
        time_filter.distance_by_signature,
    )
    ranked_all = [
        replace(item, rank=index)
        for index, item in enumerate(ranked_all, start=1)
    ]

    if search.preferred_carriers:
        ranked_visible = ranked_all[: search.max_options]
    else:
        ranked_visible = _diversify_ranked_by_carrier(
            ranked_all,
            search.max_options,
        )

    # Carrier diversification chooses WHICH options survive, but temporal
    # intent must remain authoritative for their final display order.
    ranked_visible = reorder_ranked_by_time(
        ranked_visible,
        time_filter.distance_by_signature,
    )
    ranked_visible = [
        replace(item, rank=index)
        for index, item in enumerate(ranked_visible, start=1)
    ]

    # Keep additional ranked candidates from the SAME BFM response. The UI
    # receives five at a time; clicking "Ver 5 más" does not call Sabre again.
    visible_option_ids = {
        id(item.option)
        for item in ranked_visible
    }
    remaining_ranked = [
        item
        for item in ranked_all
        if id(item.option) not in visible_option_ids
    ]
    expanded_ranked = (ranked_visible + remaining_ranked)[:50]
    expanded_ranked = [
        replace(item, rank=index)
        for index, item in enumerate(expanded_ranked, start=1)
    ]
    expanded_ranked = assign_commercial_labels(
        expanded_ranked,
        time_distance_by_signature=time_filter.distance_by_signature,
        has_time_constraints=bool(search.time_constraints),
    )

    visible_count = min(search.max_options, len(expanded_ranked))
    ranked = expanded_ranked[:visible_count]
    candidate_ranked = expanded_ranked[visible_count:]

    def _api_ranked_option(item) -> RankedOption:
        return RankedOption(
            rank=item.rank,
            score=item.score,
            stops=item.stops,
            duration_minutes=item.duration_minutes,
            ranking_currency=item.ranking_currency,
            ranking_price=item.ranking_price,
            commercial_labels=list(item.commercial_labels),
            itinerary=item.option,
        )

    response = QuoteSearchAPIResponse(
        environment=settings.sabre_env,
        effective_currencies=[key for key in keys if key != "OFFICIAL"],
        calls=calls,
        result_count=len(normalized),
        available_option_count=len(expanded_ranked),
        options=[
            _api_ranked_option(item)
            for item in ranked
        ],
        candidate_options=[
            _api_ranked_option(item)
            for item in candidate_ranked
        ],
        client_quote=render_ranked_client_quote(ranked),
        time_match=time_filter.diagnostics,
    )

    if request.persist:
        repository = get_quote_repository()
        _persist_started = perf_counter()
        quote_id = repository.create(request=request, response=response)
        _persist_seconds = perf_counter() - _persist_started
        response.quote_id = quote_id
        print(f"[QUOTE] SQLite create: {_persist_seconds:.3f}s")

    _quote_total = perf_counter() - _quote_started
    _other_seconds = max(
        0.0,
        _quote_total - _bfm_wall_seconds - _normalize_seconds - _persist_seconds,
    )
    print(
        f"[QUOTE] search total: {_quote_total:.3f}s | "
        f"BFM wall={_bfm_wall_seconds:.3f}s | "
        f"BFM service={_bfm_seconds:.3f}s ({_bfm_calls} calls) | "
        f"normalize={_normalize_seconds:.3f}s | "
        f"SQLite={_persist_seconds:.3f}s | "
        f"other={_other_seconds:.3f}s"
    )

    return response
