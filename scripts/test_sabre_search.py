import argparse
import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.models.quote_request import Cabin, FarePreference, QuoteSearchRequest, RequestProfile, SearchLeg, TripType
from app.sabre.client import SabreClient
from app.sabre.errors import SabreError
from app.sabre.shopping import (
    SabreShoppingService,
    build_bfm_request,
    extract_bfm_diagnostics,
)
from app.services.normalizer import merge_cabin_itineraries, merge_currency_itineraries, normalize_bfm_response
from app.services.pricing_rules import PricingCurrency, pricing_modifier, resolve_pricing_currencies_for_legs
from app.services.quote_renderer import render_ranked_client_quote
from app.services.ranking import RankingMode, rank_itineraries
from app.support import create_support_bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba una búsqueda real en Sabre BFM v5")
    parser.add_argument("--env", choices=["prod", "cert"], default="prod")
    parser.add_argument("--origin")
    parser.add_argument("--destination")
    parser.add_argument("--departure")
    parser.add_argument("--return-date")
    parser.add_argument(
        "--trip-type",
        choices=[t.value for t in TripType],
        default=None,
        help="one_way, round_trip, open_jaw, circle_trip o multi_city",
    )
    parser.add_argument(
        "--leg",
        action="append",
        default=[],
        metavar="ORIGEN,DESTINO,AAAA-MM-DD[,HH:MM]",
        help="Tramo explícito; repetir para circle_trip/multi_city/open_jaw.",
    )
    parser.add_argument("--return-origin", help="Origen del regreso para open jaw")
    parser.add_argument("--return-destination", help="Destino del regreso para open jaw")
    parser.add_argument("--departure-time", default="12:00:00")
    parser.add_argument("--return-time", default="12:00:00")
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--child-age", type=int, default=6)
    parser.add_argument("--infants", type=int, default=0)
    parser.add_argument("--cabin", choices=[c.value for c in Cabin], default="ECONOMY")
    parser.add_argument("--max-stops", type=int, default=1)
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Alias de --max-stops 0: sólo vuelos directos.",
    )
    parser.add_argument("--max-options", type=int, default=5)
    parser.add_argument(
        "--currency",
        choices=[c.value for c in PricingCurrency],
        default=PricingCurrency.AUTO.value,
        help="AUTO: USD internacional / ARS doméstico Argentina; BOTH: USD+ARS internacional",
    )
    parser.add_argument("--carrier", action="append", default=[])
    parser.add_argument(
        "--exclude-carrier",
        action="append",
        default=[],
        help="Excluye una aerolínea; puede repetirse.",
    )
    parser.add_argument(
        "--fare-preference",
        choices=[f.value for f in FarePreference],
        default=FarePreference.AUTO.value,
        help="lowest, baggage (requiere pieza gratis), branded (hasta 3 brands) o auto",
    )
    parser.add_argument(
        "--sort",
        choices=[m.value for m in RankingMode],
        default=RankingMode.BALANCED.value,
        help="balanced combina precio, escalas y duración; también: price, duration, stops",
    )
    parser.add_argument(
        "--profile",
        choices=[p.value for p in RequestProfile],
        default=RequestProfile.STANDARD.value,
        help="standard para cotizar; official solo para diagnóstico mínimo",
    )
    parser.add_argument("--dry-run", action="store_true", help="Genera payload sin llamar a Sabre")
    parser.add_argument(
        "--business-companion",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="En branded/auto, hace una búsqueda Business adicional y la une sólo a vuelos idénticos.",
    )
    parser.add_argument(
        "--support-bundle",
        action="store_true",
        help="Genera ZIP sanitizado para adjuntar a soporte Sabre",
    )
    return parser.parse_args()


def _parse_leg(value: str) -> SearchLeg:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) not in {3, 4}:
        raise ValueError(f"--leg inválido: {value}. Usar ORIGEN,DESTINO,AAAA-MM-DD[,HH:MM]")
    data = {
        "origin": parts[0],
        "destination": parts[1],
        "departure_date": parts[2],
    }
    if len(parts) == 4:
        data["departure_time"] = parts[3]
    return SearchLeg(**data)


def _build_search(args: argparse.Namespace) -> QuoteSearchRequest:
    explicit_legs = [_parse_leg(value) for value in args.leg]

    if explicit_legs:
        origin = explicit_legs[0].origin
        destination = explicit_legs[0].destination
        departure = explicit_legs[0].departure_date
        trip_type = args.trip_type or (
            TripType.CIRCLE_TRIP.value if len(explicit_legs) >= 3 else TripType.OPEN_JAW.value
        )
    else:
        if not args.origin or not args.destination or not args.departure:
            raise ValueError("--origin, --destination y --departure son obligatorios salvo que uses --leg")
        origin = args.origin
        destination = args.destination
        departure = args.departure
        trip_type = args.trip_type or (
            TripType.ROUND_TRIP.value if args.return_date else TripType.ONE_WAY.value
        )

        if trip_type == TripType.OPEN_JAW.value:
            if not args.return_date or not args.return_origin or not args.return_destination:
                raise ValueError(
                    "open_jaw requiere --return-date, --return-origin y --return-destination"
                )
            explicit_legs = [
                SearchLeg(
                    origin=origin,
                    destination=destination,
                    departure_date=departure,
                    departure_time=args.departure_time,
                ),
                SearchLeg(
                    origin=args.return_origin,
                    destination=args.return_destination,
                    departure_date=args.return_date,
                    departure_time=args.return_time,
                ),
            ]

    return QuoteSearchRequest(
        origin=origin,
        destination=destination,
        departure_date=departure,
        return_date=args.return_date if not explicit_legs else None,
        departure_time=args.departure_time,
        return_time=args.return_time,
        trip_type=trip_type,
        legs=explicit_legs,
        adults=args.adults,
        children=args.children,
        child_age=args.child_age,
        infants=args.infants,
        cabin=args.cabin,
        max_stops=0 if args.direct else args.max_stops,
        max_options=args.max_options,
        currency=args.currency,
        preferred_carriers=args.carrier,
        excluded_carriers=args.exclude_carrier,
        request_profile=args.profile,
        fare_preference=args.fare_preference,
    )


async def main() -> None:
    args = parse_args()
    search = _build_search(args)
    output = Path("output")
    output.mkdir(exist_ok=True)
    settings = get_settings(args.env)

    currencies = resolve_pricing_currencies_for_legs(search.effective_legs(), search.currency)

    print(f"Entorno: {settings.sabre_env}")
    print(f"OAuth: {settings.sabre_token_type}")
    print(f"Endpoint base: {settings.base_url}")
    print(f"Perfil enviado: {search.request_profile.value}")
    if search.request_profile == RequestProfile.STANDARD:
        print(
            "Pricing: "
            + ", ".join(f"{currency} ({pricing_modifier(currency)})" for currency in currencies)
        )
        print(f"Tipo de viaje: {search.trip_type.value}; tramos: {len(search.effective_legs())}")
        print(f"Preferencia tarifaria: {search.fare_preference.value}")
        if str(search.currency) in {"USD", "BOTH"} and currencies == ["ARS"]:
            print("Regla aplicada: todos los tramos son domésticos Argentina, moneda forzada a ARS (MARS).")

    payloads = {
        currency: build_bfm_request(search, settings.sabre_pcc, currency_override=currency)
        for currency in currencies
    }
    if search.request_profile == RequestProfile.OFFICIAL:
        payloads = {"OFFICIAL": build_bfm_request(search, settings.sabre_pcc)}

    add_business = (
        args.business_companion
        and search.request_profile == RequestProfile.STANDARD
        and search.fare_preference in {FarePreference.BRANDED, FarePreference.AUTO}
        and search.cabin not in {Cabin.BUSINESS, Cabin.FIRST}
    )
    business_search = search.model_copy(update={"cabin": Cabin.BUSINESS}) if add_business else None

    if args.dry_run:
        for currency, payload in payloads.items():
            suffix = currency.lower()
            target = output / f"shopping_request_{suffix}.json"
            target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Payload {currency}: {target}")
            if business_search is not None and currency != "OFFICIAL":
                business_payload = build_bfm_request(
                    business_search, settings.sabre_pcc, currency_override=currency
                )
                business_target = output / f"shopping_request_{suffix}_business.json"
                business_target.write_text(
                    json.dumps(business_payload, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                print(f"Payload {currency} Business: {business_target}")
        return

    client: SabreClient | None = None
    raw_by_currency: dict[str, dict] = {}
    diagnostics_by_currency: dict[str, dict] = {}
    normalized_by_currency: dict[str, list] = {}
    business_normalized_by_currency: dict[str, list] = {}
    error_text: str | None = None

    try:
        client = SabreClient(settings)
        async with client:
            for currency in payloads:
                override = None if currency == "OFFICIAL" else currency
                raw = await SabreShoppingService(client, settings).search(
                    search, currency_override=override
                )
                raw_by_currency[currency] = raw
                diagnostics_by_currency[currency] = extract_bfm_diagnostics(raw)
                normalized_by_currency[currency] = normalize_bfm_response(raw)

                raw_target = output / f"raw_sabre_response_{currency.lower()}.json"
                raw_target.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
                print(
                    f"{currency}: Transaction ID "
                    f"{diagnostics_by_currency[currency].get('transaction_id') or 'no informado'}, "
                    f"itinerarios {diagnostics_by_currency[currency].get('itinerary_count', 0)}"
                )

                if business_search is not None and currency != "OFFICIAL":
                    business_raw = await SabreShoppingService(client, settings).search(
                        business_search, currency_override=currency
                    )
                    business_diag = extract_bfm_diagnostics(business_raw)
                    business_normalized_by_currency[currency] = normalize_bfm_response(business_raw)
                    business_target = output / f"raw_sabre_response_{currency.lower()}_business.json"
                    business_target.write_text(
                        json.dumps(business_raw, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    print(
                        f"{currency} Business: Transaction ID "
                        f"{business_diag.get('transaction_id') or 'no informado'}, "
                        f"itinerarios {business_diag.get('itinerary_count', 0)}"
                    )

        for currency, business_options in business_normalized_by_currency.items():
            normalized_by_currency[currency] = merge_cabin_itineraries(
                normalized_by_currency.get(currency, []),
                business_options,
                cabins={"business"},
            )

        keys = list(normalized_by_currency)
        normalized = normalized_by_currency[keys[0]] if keys else []
        if len(keys) == 2:
            normalized = merge_currency_itineraries(
                normalized_by_currency[keys[0]], normalized_by_currency[keys[1]]
            )

        normalized_target = output / "normalized_itineraries.json"
        normalized_target.write_text(
            json.dumps([item.model_dump(mode="json") for item in normalized], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        quote_target = output / "client_quote.txt"
        ranking_currency = "ARS" if normalized and normalized[0].is_domestic_argentina else "USD"
        ranked = rank_itineraries(
            normalized,
            mode=args.sort,
            preferred_currency=ranking_currency,
        )[: search.max_options]
        ranking_target = output / "ranked_itineraries.json"
        ranking_target.write_text(
            json.dumps(
                [
                    {
                        "rank": item.rank,
                        "score": str(item.score),
                        "stops": item.stops,
                        "duration_minutes": item.duration_minutes,
                        "ranking_currency": item.ranking_currency,
                        "ranking_price": str(item.ranking_price),
                        "source_index": item.option.source_index,
                    }
                    for item in ranked
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        quote = render_ranked_client_quote(ranked)
        quote_target.write_text(quote, encoding="utf-8")
        print(f"Ranking ({args.sort}): {ranking_target}")
        print(f"Itinerarios normalizados: {normalized_target}")
        print(f"Cotización: {quote_target}")

    except SabreError as exc:
        error_text = str(exc)
        print(f"ERROR SABRE: {error_text}")
        raise
    finally:
        if args.support_bundle and client is not None:
            # Support bundle currently captures the last BFM exchange. For BOTH,
            # the last exchange is ARS; individual raw files preserve both calls.
            last_key = list(diagnostics_by_currency)[-1] if diagnostics_by_currency else None
            diagnostics = diagnostics_by_currency.get(last_key, {}) if last_key else {}
            bundle_payload = payloads.get(last_key, next(iter(payloads.values())))
            bundle = create_support_bundle(
                settings=settings,
                search=search,
                payload=bundle_payload,
                exchange=client.last_exchange,
                diagnostics=diagnostics,
                error=error_text,
            )
            print(f"Support bundle: {bundle}")
            print("Bundle sanitizado: no contiene token, password ni Client Secret.")


if __name__ == "__main__":
    asyncio.run(main())
