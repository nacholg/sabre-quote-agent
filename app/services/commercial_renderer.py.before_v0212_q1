from __future__ import annotations

from html import escape
import re

from app.models.api import QuoteRenderResponse, StoredQuoteRecord
from app.models.commercial_quote import CommercialFare, CommercialOption, CommercialQuote
from app.services.commercial_quote_builder import build_commercial_quote
from app.services.quote_renderer import _money


MONTHS_ES = [
    "ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
    "JUL", "AGO", "SEP", "OCT", "NOV", "DIC",
]


def _date_es(value) -> str:
    return f"{value.day:02d}{MONTHS_ES[value.month - 1]}"


def _time(value) -> str:
    return value.strftime("%H%M")



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


def _passenger_price_lines(fare: CommercialFare) -> list[str]:
    passenger_prices = list(
        getattr(fare, 'passenger_prices', None) or []
    )

    mixed = (
        len(passenger_prices) > 1
        or any(
            str(passenger.passenger_type or '').upper() != 'ADT'
            for passenger in passenger_prices
        )
    )

    if not mixed:
        return [
            f"{fare.currency} {_money(fare.price_per_passenger, fare.currency)}"
        ]

    lines: list[str] = []
    for passenger in passenger_prices:
        currency = passenger.currency or fare.currency
        lines.append(
            f"{_passenger_label(passenger)} ×{passenger.quantity}: "
            f"{currency} {_money(passenger.unit_price, currency)}"
        )

    if fare.total_price is not None:
        lines.append(
            f"Total: {fare.currency} {_money(fare.total_price, fare.currency)}"
        )
    return lines

def _fare_lines(
    fare: CommercialFare,
    option: CommercialOption,
    *,
    commercial_summary=None,
) -> list[str]:
    label = (
        getattr(fare, 'brand_name', None)
        or getattr(fare, 'brand_code', None)
        or str(getattr(fare, 'cabin', '')).upper()
    )
    passenger_price_lines = _passenger_price_lines(fare)
    mixed_passengers = len(passenger_price_lines) > 1

    if mixed_passengers:
        lines = [label]
        lines.extend(passenger_price_lines)
    else:
        lines = [f"{label} — {passenger_price_lines[0]}"]

    if commercial_summary is not None:
        baggage = getattr(commercial_summary, 'baggage', None)
        changes = getattr(commercial_summary, 'changes', None)
        refunds = getattr(commercial_summary, 'refunds', None)
        no_show = getattr(commercial_summary, 'no_show', None)
    else:
        rules = getattr(fare, 'rules', None)
        baggage = getattr(rules, 'baggage', None)
        changes = getattr(rules, 'changes', None)
        refunds = getattr(rules, 'refunds', None)
        no_show = getattr(rules, 'no_show', None)

    if baggage:
        lines.append(f"Equipaje: {_without_prefix(baggage, 'Equipaje')}")
    if changes:
        lines.append(f"Cambios: {_without_prefix(changes, 'Cambios')}")
    if refunds:
        refund_text = refunds.strip()
        if refund_text.lower().startswith("devoluciones:"):
            lines.append(refund_text)
        else:
            lines.append(
                f"Devolución: "
                f"{_without_prefix(refund_text, 'Devoluciones', 'Devolución')}"
            )
    if no_show:
        lines.append(f"No-show: {_without_prefix(no_show, 'No-show')}")

    domestic_flag = getattr(option, 'is_domestic_argentina', None)
    if domestic_flag is None:
        segments = list(getattr(option, 'segments', None) or [])
        domestic_flag = bool(segments) and all(
            getattr(segment, 'departure_country', None) == 'AR'
            and getattr(segment, 'arrival_country', None) == 'AR'
            for segment in segments
        )

    q1_amount = getattr(fare, 'q1_amount', None)
    q1_currency = getattr(fare, 'q1_currency', None)
    currency = getattr(fare, 'currency', '')

    if currency == 'ARS' and not domestic_flag and q1_amount is not None:
        lines.append(
            f"Q1 incluido: {q1_currency or 'ARS'} "
            f"{_money(q1_amount, q1_currency or 'ARS')}"
        )

    return lines

def _route_header(quote: CommercialQuote) -> list[str]:
    if quote.legs:
        if len(quote.legs) == 1:
            leg = quote.legs[0]
            return [f"*{leg.origin} – {leg.destination}*", str(leg.departure_date)]
        return [
            '*Itinerario*',
            *[
                f"{index}. {leg.origin} – {leg.destination} {leg.departure_date}"
                for index, leg in enumerate(quote.legs, start=1)
            ],
        ]
    return []


def render_whatsapp(record: StoredQuoteRecord) -> str:
    quote = build_commercial_quote(record)
    lines = _route_header(quote)
    lines.append('')

    for number, option in enumerate(quote.options, start=1):
        lines.append(f"*Opción {number}*")
        for segment in option.segments:
            arrival_suffix = (
                f" {_date_es(segment.arrival_at)}"
                if segment.arrival_at.date() != segment.departure_at.date()
                else ''
            )
            lines.append(
                f"{segment.marketing_carrier} {segment.flight_number} "
                f"{_date_es(segment.departure_at)} "
                f"{segment.departure_airport}/{segment.arrival_airport} "
                f"{_time(segment.departure_at)} {_time(segment.arrival_at)}"
                f"{arrival_suffix}"
            )
        lines.append('')
        for fare in option.fares:
            lines.extend(_fare_lines(fare, option))
            lines.append('')
        lines.append('')

    lines.append('Tarifas sujetas a disponibilidad al momento de emisión.')
    lines.append(f"Referencia: {quote.quote_id}")
    return '\n'.join(lines).strip() + '\n'

def render_email_html(record: StoredQuoteRecord) -> str:
    quote = build_commercial_quote(record)

    if quote.legs:
        if len(quote.legs) == 1:
            leg = quote.legs[0]
            route = f"{leg.origin} – {leg.destination}"
            dates = str(leg.departure_date)
        else:
            route = 'Itinerario'
            dates = ' · '.join(
                f"{leg.origin}–{leg.destination} {leg.departure_date}"
                for leg in quote.legs
            )
    else:
        route = ''
        dates = ''

    option_html: list[str] = []
    for number, option in enumerate(quote.options, start=1):
        segments = ''.join(
            '<tr>'
            f"<td>{escape(seg.marketing_carrier)} {escape(seg.flight_number)}</td>"
            f"<td>{escape(_date_es(seg.departure_at))}</td>"
            f"<td>{escape(seg.departure_airport)} / {escape(seg.arrival_airport)}</td>"
            f"<td>{escape(_time(seg.departure_at))}</td>"
            f"<td>{escape(_time(seg.arrival_at))}</td>"
            '</tr>'
            for seg in option.segments
        )
        fares_html: list[str] = []
        for fare in option.fares:
            lines = _fare_lines(fare, option)
            title = escape(lines[0])
            details = ''.join(f"<li>{escape(line)}</li>" for line in lines[1:])
            fares_html.append(
                f"<div style='margin:12px 0'><strong>{title}</strong>"
                f"<ul>{details}</ul></div>"
            )
        option_html.append(
            f"<h3>Opción {number}</h3>"
            "<table style='border-collapse:collapse;width:100%' border='1' cellpadding='6'>"
            '<thead><tr><th>Vuelo</th><th>Fecha</th><th>Ruta</th><th>Salida</th><th>Llegada</th></tr></thead>'
            f"<tbody>{segments}</tbody></table>"
            + ''.join(fares_html)
        )
    return (
        "<!doctype html><html><body style='font-family:Arial,sans-serif'>"
        f"<h2>{escape(route)}</h2>"
        f"<p>{escape(dates)}</p>"
        + ''.join(option_html)
        + '<p><em>Tarifas sujetas a disponibilidad al momento de emisión.</em></p>'
        f"<p>Referencia: {escape(quote.quote_id)}</p>"
        '</body></html>'
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
