from __future__ import annotations

from app.models.api import StoredQuoteRecord
from app.models.commercial_quote import (
    CommercialQuoteDocument,
    CommercialQuoteFare,
    CommercialQuoteOption,
    CommercialQuoteSegment,
)
from app.models.itinerary import ItineraryOption
from app.services.quote_renderer import (
    _commercial_brand_features,
    _fare_baggage_line,
    _select_commercial_fares,
)


def _selected_items(record: StoredQuoteRecord) -> list[dict]:
    if not record.selected_ranks:
        raise ValueError(
            "La cotización no tiene opciones seleccionadas. "
            "Usá POST /quotes/{quote_id}/select antes de preparar la cotización comercial."
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


def _commercial_fares(option: ItineraryOption):
    selected = []
    currencies = option.fare_options_by_currency or {}

    for currency in ("USD", "ARS"):
        selected.extend(
            _select_commercial_fares(
                currencies.get(currency) or []
            )
        )

    if selected:
        return selected

    fares = list((option.fares_by_currency or {}).values())
    return fares or [option.fare]


def _passenger_count(record: StoredQuoteRecord) -> int:
    request = record.search_request or {}

    passengers = request.get("passengers") or []
    if passengers:
        return sum(int(item.get("quantity", 0)) for item in passengers)

    return sum(
        int(request.get(key) or 0)
        for key in ("adults", "children", "infants")
    )


def build_commercial_quote(
    record: StoredQuoteRecord,
) -> CommercialQuoteDocument:
    request = record.search_request or {}
    options: list[CommercialQuoteOption] = []

    for display_number, item in enumerate(
        _selected_items(record),
        start=1,
    ):
        itinerary = ItineraryOption.model_validate(item["itinerary"])

        segments = [
            CommercialQuoteSegment(
                marketing_carrier=segment.marketing_carrier,
                flight_number=segment.flight_number,
                departure_airport=segment.departure_airport,
                arrival_airport=segment.arrival_airport,
                departure_at=segment.departure_at,
                arrival_at=segment.arrival_at,
            )
            for segment in itinerary.segments
        ]

        fares = [
            CommercialQuoteFare(
                cabin=fare.cabin,
                brand_name=fare.brand_name,
                brand_code=fare.brand_code,
                currency=fare.currency,
                price_per_passenger=fare.price_per_passenger,
                total_price=fare.total_price,
                baggage=_fare_baggage_line(fare),
                conditions=_commercial_brand_features(fare),
                fare_basis_codes=fare.fare_basis_codes,
                last_ticket_date=fare.last_ticket_date,
                q1_amount=(
                    fare.q1_amount
                    if fare.currency == "ARS"
                    and not itinerary.is_domestic_argentina
                    else None
                ),
                q1_currency=(
                    fare.q1_currency
                    if fare.currency == "ARS"
                    and not itinerary.is_domestic_argentina
                    else None
                ),
            )
            for fare in _commercial_fares(itinerary)
        ]

        options.append(
            CommercialQuoteOption(
                source_rank=int(item["rank"]),
                display_number=display_number,
                segments=segments,
                fares=fares,
            )
        )

    return CommercialQuoteDocument(
        quote_id=record.quote_id,
        client_name=record.client_name,
        client_reference=record.client_reference,
        notes=record.notes,
        origin=request.get("origin"),
        destination=request.get("destination"),
        departure_date=(
            str(request.get("departure_date"))
            if request.get("departure_date")
            else None
        ),
        return_date=(
            str(request.get("return_date"))
            if request.get("return_date")
            else None
        ),
        passenger_count=_passenger_count(record),
        options=options,
    )
