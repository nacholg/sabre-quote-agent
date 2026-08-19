from __future__ import annotations

from decimal import Decimal

from app.models.api import StoredQuoteRecord
from app.models.commercial_quote import (
    CommercialFare,
    CommercialFareRules,
    CommercialOption,
    CommercialPassengerPrice,
    CommercialQuote,
)
from app.models.itinerary import FareOption, ItineraryOption
from app.models.quote_request import SearchLeg, infer_trip_type
from app.services.live_air_rules_audit import audit_stored_quote_live
from app.services.quote_renderer import (
    _commercial_brand_features,
    _select_commercial_fares,
)


def _price_key(value) -> str:
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except Exception:
        return str(value or "")


def _fare_key(rank: int, fare_like) -> tuple[int, str, str, str]:
    brand = (
        getattr(fare_like, "brand_name", None)
        or getattr(fare_like, "brand_code", None)
        or ""
    ).strip().upper()
    return (
        int(rank),
        brand,
        (getattr(fare_like, "currency", None) or "").strip().upper(),
        _price_key(getattr(fare_like, "price_per_passenger", None)),
    )


def _selected_items(record: StoredQuoteRecord) -> list[dict]:
    if not record.selected_ranks:
        raise ValueError(
            "La cotización no tiene opciones seleccionadas. "
            "Usá POST /quotes/{quote_id}/select antes de construir CommercialQuote."
        )

    by_rank = {
        int(item["rank"]): item
        for item in (record.quote_response.get("options") or [])
        if item.get("rank") is not None
    }
    missing = [rank for rank in record.selected_ranks if rank not in by_rank]
    if missing:
        raise ValueError(
            "La selección guardada referencia opciones inexistentes: "
            + ", ".join(str(rank) for rank in missing)
        )
    return [by_rank[rank] for rank in record.selected_ranks]


def _commercial_fares(option: ItineraryOption) -> list[FareOption]:
    selected: list[FareOption] = []
    currencies = option.fare_options_by_currency or {}
    for currency in ("USD", "ARS"):
        selected.extend(_select_commercial_fares(currencies.get(currency) or []))
    if selected:
        return selected

    fares = list((option.fares_by_currency or {}).values())
    return fares or [option.fare]


def _commercial_rule_map(record: StoredQuoteRecord) -> dict:
    try:
        audit = audit_stored_quote_live(record, selected_only=True)
    except Exception:
        return {}

    result = {}
    for option in audit.options:
        for fare in option.fares:
            summary = getattr(fare, "commercial_summary", None)
            if summary is not None:
                result[_fare_key(option.rank, fare)] = summary
    return result


def _fallback_baggage(fare: FareOption) -> str | None:
    baggage = list(getattr(fare, "baggage", None) or [])
    return " ".join(
        str(item).strip()
        for item in baggage
        if str(item).strip()
    ) or None


def _passenger_prices(
    fare: FareOption,
) -> list[CommercialPassengerPrice]:
    result: list[CommercialPassengerPrice] = []

    for passenger in list(
        getattr(fare, "passenger_prices", None) or []
    ):
        currency = getattr(passenger, "currency", None) or fare.currency
        unit_price = Decimal(str(getattr(passenger, "unit_price")))
        quantity = int(getattr(passenger, "quantity", 1) or 1)

        raw_total = getattr(passenger, "total_price", None)
        total_price = (
            Decimal(str(raw_total))
            if raw_total is not None
            else unit_price * quantity
        )

        result.append(
            CommercialPassengerPrice(
                passenger_type=str(
                    getattr(passenger, "passenger_type", "")
                ),
                quantity=quantity,
                age=getattr(passenger, "age", None),
                currency=currency,
                unit_price=unit_price,
                total_price=total_price,
            )
        )

    return result



def _fallback_rule_texts(
    fare: FareOption,
) -> tuple[str | None, str | None]:
    changes = None
    refunds = None

    for line in _commercial_brand_features(fare):
        value = line.strip()
        if value.startswith("Cambios:"):
            changes = value
        elif (
            value.startswith("Devoluciones:")
            or value.startswith("Devolución:")
        ):
            refunds = value

    return changes, refunds



def _commercial_fare(
    rank: int,
    fare: FareOption,
    summaries: dict,
) -> CommercialFare:
    summary = summaries.get(_fare_key(rank, fare))
    fallback_changes, fallback_refunds = _fallback_rule_texts(fare)

    rules = CommercialFareRules(
        baggage=(
            getattr(summary, "baggage", None)
            if summary is not None
            else _fallback_baggage(fare)
        ),
        changes=(
            getattr(summary, "changes", None)
            if summary is not None
            else fallback_changes
        ),
        refunds=(
            getattr(summary, "refunds", None)
            if summary is not None
            else fallback_refunds
        ),
        no_show=(
            getattr(summary, "no_show", None)
            if summary is not None
            else None
        ),
    )

    return CommercialFare(
        cabin=fare.cabin,
        currency=fare.currency,
        brand_name=fare.brand_name,
        brand_code=fare.brand_code,
        price_per_passenger=fare.price_per_passenger,
        total_price=fare.total_price,
        passenger_prices=_passenger_prices(fare),
        fare_basis_codes=list(fare.fare_basis_codes or []),
        validating_carrier=fare.validating_carrier,
        q1_amount=fare.q1_amount,
        q1_currency=fare.q1_currency,
        rules=rules,
    )


def _request_trip_shape(
    request: dict,
) -> tuple[list[SearchLeg], str | None]:
    raw_legs = list(request.get("legs") or [])

    if raw_legs:
        legs = [
            SearchLeg.model_validate(item)
            for item in raw_legs
        ]
    else:
        origin = request.get("origin")
        destination = request.get("destination")
        departure_date = request.get("departure_date")
        return_date = request.get("return_date")

        legs: list[SearchLeg] = []

        if origin and destination and departure_date:
            legs.append(
                SearchLeg(
                    origin=origin,
                    destination=destination,
                    departure_date=departure_date,
                    departure_time=(
                        request.get("departure_time")
                        or "12:00:00"
                    ),
                )
            )

            if return_date:
                legs.append(
                    SearchLeg(
                        origin=destination,
                        destination=origin,
                        departure_date=return_date,
                        departure_time=(
                            request.get("return_time")
                            or "12:00:00"
                        ),
                    )
                )

    raw_trip_type = request.get("trip_type")

    if raw_trip_type:
        trip_type = getattr(
            raw_trip_type,
            "value",
            raw_trip_type,
        )
    elif legs:
        trip_type = infer_trip_type(legs).value
    else:
        trip_type = None

    return (
        legs,
        str(trip_type) if trip_type is not None else None,
    )


def build_commercial_quote(
    record: StoredQuoteRecord,
) -> CommercialQuote:
    items = _selected_items(record)
    summaries = _commercial_rule_map(record)
    legs, trip_type = _request_trip_shape(record.search_request)

    options: list[CommercialOption] = []

    for item in items:
        rank = int(item["rank"])
        itinerary = ItineraryOption.model_validate(item["itinerary"])

        labels = [
            getattr(label, "value", label)
            for label in (item.get("commercial_labels") or [])
        ]

        options.append(
            CommercialOption(
                rank=rank,
                score=item.get("score"),
                stops=item.get("stops"),
                duration_minutes=item.get("duration_minutes"),
                commercial_labels=[str(label) for label in labels],
                segments=itinerary.segments,
                fares=[
                    _commercial_fare(rank, fare, summaries)
                    for fare in _commercial_fares(itinerary)
                ],
            )
        )

    return CommercialQuote(
        quote_id=record.quote_id,
        environment=str(
            record.quote_response.get("environment") or ""
        ).upper(),
        trip_type=trip_type,
        legs=legs,
        client_name=record.client_name,
        client_reference=record.client_reference,
        options=options,
    )
