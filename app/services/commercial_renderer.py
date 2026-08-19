from __future__ import annotations

from html import escape
import re

from app.models.api import QuoteRenderResponse, StoredQuoteRecord
from app.services.live_air_rules_audit import audit_stored_quote_live
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


def _price_key(value) -> str:
    try:
        from decimal import Decimal
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


def _commercial_rule_map(record: StoredQuoteRecord) -> dict:
    """
    Resolve Air Fare Rules once for the selected quote.
    Any SOAP/AirRules failure falls back to the existing renderer behavior.
    """
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


def _without_prefix(value: str | None, *prefixes: str) -> str:
    text = (value or "").strip()
    lower = text.lower()
    for prefix in prefixes:
        if lower.startswith(prefix.lower()):
            return text[len(prefix):].lstrip(" :;-")
    return text


def _passenger_label(passenger) -> str:
    ptc = str(
        getattr(passenger, "passenger_type", "") or ""
    ).upper()
    if ptc == "ADT":
        return "Adulto"
    if ptc == "INF":
        return "Infante"
    match = re.fullmatch(r"C(\d{1,2})", ptc)
    if match:
        return f"Niño {int(match.group(1))} años"
    if ptc in {"CHD", "CNN"}:
        return "Niño"
    return ptc or "Pasajero"


def _passenger_price_lines(fare: FareOption) -> list[str]:
    passenger_prices = list(
        getattr(fare, "passenger_prices", None) or []
    )

    mixed = (
        len(passenger_prices) > 1
        or any(
            str(getattr(p, "passenger_type", "")).upper()
            != "ADT"
            for p in passenger_prices
        )
    )

    if not mixed:
        return [
            f"{fare.currency} "
            f"{_money(fare.price_per_passenger, fare.currency)}"
        ]

    lines = []
    for passenger in passenger_prices:
        currency = passenger.currency or fare.currency
        lines.append(
            f"{_passenger_label(passenger)} "
            f"×{passenger.quantity}: "
            f"{currency} "
            f"{_money(passenger.unit_price, currency)}"
        )

    if fare.total_price is not None:
        lines.append(
            f"Total: {fare.currency} "
            f"{_money(fare.total_price, fare.currency)}"
        )

    return lines


def _fare_lines(
    fare: FareOption,
    option: ItineraryOption,
    *,
    commercial_summary=None,
) -> list[str]:
    label = fare.brand_name or fare.brand_code or fare.cabin.upper()
    passenger_price_lines = _passenger_price_lines(fare)
    mixed_passengers = len(passenger_price_lines) > 1

    if mixed_passengers:
        lines = [label]
        lines.extend(passenger_price_lines)
    else:
        lines = [f"{label} — {passenger_price_lines[0]}"]

    if commercial_summary is not None:
        baggage = _without_prefix(
            getattr(commercial_summary, "baggage", None), "Equipaje"
        )
        changes = _without_prefix(
            getattr(commercial_summary, "changes", None), "Cambios"
        )
        refunds = _without_prefix(
            getattr(commercial_summary, "refunds", None),
            "Devoluciones",
            "Devolución",
        )
        no_show = _without_prefix(
            getattr(commercial_summary, "no_show", None), "No-show"
        )

        if baggage:
            lines.append(f"Equipaje: {baggage}")
        if changes:
            lines.append(f"Cambios: {changes}")
        if refunds:
            lines.append(f"Devolución: {refunds}")
        if no_show:
            lines.append(f"No-show: {no_show}")
    else:
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
    summaries = _commercial_rule_map(record)
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
        rank = int(item["rank"])
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
            summary = summaries.get(_fare_key(rank, fare))
            lines.extend(
                _fare_lines(
                    fare,
                    option,
                    commercial_summary=summary,
                )
            )
            lines.append("")
        lines.append("")

    lines.append("Tarifas sujetas a disponibilidad al momento de emisión.")
    lines.append(f"Referencia: {record.quote_id}")
    return "\n".join(lines).strip() + "\n"


def render_email_html(record: StoredQuoteRecord) -> str:
    items = _selected_items(record)
    summaries = _commercial_rule_map(record)
    request = record.search_request
    route = f"{request.get('origin', '')} – {request.get('destination', '')}".strip()
    dates = str(request.get("departure_date") or "")
    if request.get("return_date"):
        dates += f" – {request['return_date']}"

    option_html: list[str] = []
    for number, item in enumerate(items, start=1):
        rank = int(item["rank"])
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
            summary = summaries.get(_fare_key(rank, fare))
            lines = _fare_lines(
                fare,
                option,
                commercial_summary=summary,
            )
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
