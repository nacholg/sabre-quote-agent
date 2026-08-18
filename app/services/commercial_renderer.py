from __future__ import annotations

from html import escape

from app.models.api import QuoteRenderResponse, StoredQuoteRecord
from app.models.itinerary import FareOption, ItineraryOption
from app.services.quote_renderer import (
    AIRLINES,
    AIRPORTS,
    _commercial_brand_features,
    _fare_baggage_line,
    _money,
    _select_commercial_fares,
)


MONTHS_ES = [
    "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
    "JUL", "AGO", "SEP", "OCT", "NOV", "DIC",
]


def _date_es(value) -> str:
    return f"{value.day:02d}{MONTHS_ES[value.month - 1]}"


def _time(value) -> str:
    return value.strftime("%H%M")


def _selected_items(record: StoredQuoteRecord) -> list[dict]:
    if not record.selected_ranks:
        raise ValueError(
            "La cotización no tiene opciones seleccionadas. "
            "Usá POST /quotes/{quote_id}/select antes de renderizar."
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


def _fare_lines(fare: FareOption, option: ItineraryOption) -> list[str]:
    label = fare.brand_name or fare.brand_code or fare.cabin.upper()
    lines = [f"{label} — {fare.currency} {_money(fare.price_per_passenger, fare.currency)}"]
    lines.append(f"Equipaje: {_fare_baggage_line(fare)}")
    for feature in _commercial_brand_features(fare):
        lines.append(feature)
    if fare.currency == "ARS" and not option.is_domestic_argentina and fare.q1_amount is not None:
        lines.append(
            f"Q1 incluido: {fare.q1_currency or 'ARS'} "
            f"{_money(fare.q1_amount, fare.q1_currency or 'ARS')}"
        )
    return lines


def render_whatsapp(record: StoredQuoteRecord) -> str:
    items = _selected_items(record)
    request = record.search_request
    route = f"{request.get('origin', '')} – {request.get('destination', '')}".strip()
    departure = request.get("departure_date") or ""
    return_date = request.get("return_date")

    lines = [f"*{route}*"]
    if return_date:
        lines.append(f"{departure} – {return_date}")
    elif departure:
        lines.append(str(departure))
    lines.append("")

    for number, item in enumerate(items, start=1):
        option = ItineraryOption.model_validate(item["itinerary"])
        lines.append(f"*Opción {number}*")
        for segment in option.segments:
            arrival_suffix = (
                f" {_date_es(segment.arrival_at)}"
                if segment.arrival_at.date() != segment.departure_at.date()
                else ""
            )
            lines.append(
                f"{segment.marketing_carrier} {segment.flight_number} "
                f"{_date_es(segment.departure_at)} "
                f"{segment.departure_airport}/{segment.arrival_airport} "
                f"{_time(segment.departure_at)} {_time(segment.arrival_at)}"
                f"{arrival_suffix}"
            )
        lines.append("")
        for fare in _commercial_fares(option):
            lines.extend(_fare_lines(fare, option))
            lines.append("")
        lines.append("")

    lines.append("Tarifas sujetas a disponibilidad al momento de emisión.")
    lines.append(f"Referencia: {record.quote_id}")
    return "\n".join(lines).strip() + "\n"


def render_email_html(record: StoredQuoteRecord) -> str:
    items = _selected_items(record)
    request = record.search_request
    route = f"{request.get('origin', '')} – {request.get('destination', '')}".strip()
    dates = str(request.get("departure_date") or "")
    if request.get("return_date"):
        dates += f" – {request['return_date']}"

    option_html: list[str] = []
    for number, item in enumerate(items, start=1):
        option = ItineraryOption.model_validate(item["itinerary"])
        segments = "".join(
            "<tr>"
            f"<td>{escape(seg.marketing_carrier)} {escape(seg.flight_number)}</td>"
            f"<td>{escape(_date_es(seg.departure_at))}</td>"
            f"<td>{escape(seg.departure_airport)} / {escape(seg.arrival_airport)}</td>"
            f"<td>{escape(_time(seg.departure_at))}</td>"
            f"<td>{escape(_time(seg.arrival_at))}</td>"
            "</tr>"
            for seg in option.segments
        )

        fares_html: list[str] = []
        for fare in _commercial_fares(option):
            lines = _fare_lines(fare, option)
            title = escape(lines[0])
            details = "".join(f"<li>{escape(line)}</li>" for line in lines[1:])
            fares_html.append(
                f"<div style='margin:12px 0'><strong>{title}</strong>"
                f"<ul>{details}</ul></div>"
            )

        option_html.append(
            f"<h3>Opción {number}</h3>"
            "<table style='border-collapse:collapse;width:100%' border='1' cellpadding='6'>"
            "<thead><tr><th>Vuelo</th><th>Fecha</th><th>Ruta</th>"
            "<th>Salida</th><th>Llegada</th></tr></thead>"
            f"<tbody>{segments}</tbody></table>"
            + "".join(fares_html)
        )

    return (
        "<!doctype html><html><body style='font-family:Arial,sans-serif'>"
        f"<h2>{escape(route)}</h2>"
        f"<p>{escape(dates)}</p>"
        + "".join(option_html)
        + "<p><em>Tarifas sujetas a disponibilidad al momento de emisión.</em></p>"
        f"<p>Referencia: {escape(record.quote_id)}</p>"
        "</body></html>"
    )


def render_stored_quote(record: StoredQuoteRecord, format: str) -> QuoteRenderResponse:
    if format == "whatsapp":
        content = render_whatsapp(record)
        content_type = "text/plain; charset=utf-8"
    elif format == "email":
        content = render_email_html(record)
        content_type = "text/html; charset=utf-8"
    else:
        raise ValueError("Formato no soportado. Usá whatsapp o email.")

    return QuoteRenderResponse(
        quote_id=record.quote_id,
        format=format,
        selected_ranks=record.selected_ranks,
        content_type=content_type,
        content=content,
    )
