from __future__ import annotations

from html import escape

from app.models.api import QuoteRenderResponse, StoredQuoteRecord
from app.models.commercial_quote import (
    CommercialQuoteDocument,
    CommercialQuoteFare,
)
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


def _fare_lines(fare: CommercialQuoteFare) -> list[str]:
    label = fare.brand_name or fare.brand_code or fare.cabin.upper()
    lines = [
        f"{label} — {fare.currency} "
        f"{_money(fare.price_per_passenger, fare.currency)}"
    ]
    lines.append(f"Equipaje: {fare.baggage}")
    lines.extend(fare.conditions)

    if fare.q1_amount is not None:
        q1_currency = fare.q1_currency or "ARS"
        lines.append(
            f"Q1 incluido: {q1_currency} "
            f"{_money(fare.q1_amount, q1_currency)}"
        )

    return lines


def render_whatsapp_document(
    document: CommercialQuoteDocument,
) -> str:
    route = (
        f"{document.origin or ''} – {document.destination or ''}"
    ).strip()

    lines = [f"*{route}*"]

    if document.return_date:
        lines.append(
            f"{document.departure_date or ''} – {document.return_date}"
        )
    elif document.departure_date:
        lines.append(document.departure_date)

    lines.append("")

    for option in document.options:
        lines.append(f"*Opción {option.display_number}*")

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
                f"{_time(segment.departure_at)} "
                f"{_time(segment.arrival_at)}"
                f"{arrival_suffix}"
            )

        lines.append("")

        for fare in option.fares:
            lines.extend(_fare_lines(fare))
            lines.append("")

        lines.append("")

    lines.append(document.disclaimer)
    lines.append(f"Referencia: {document.quote_id}")

    return "\n".join(lines).strip() + "\n"


def render_email_document(
    document: CommercialQuoteDocument,
) -> str:
    route = (
        f"{document.origin or ''} – {document.destination or ''}"
    ).strip()

    dates = document.departure_date or ""
    if document.return_date:
        dates += f" – {document.return_date}"

    option_html: list[str] = []

    for option in document.options:
        segments = "".join(
            "<tr>"
            f"<td>{escape(seg.marketing_carrier)} "
            f"{escape(seg.flight_number)}</td>"
            f"<td>{escape(_date_es(seg.departure_at))}</td>"
            f"<td>{escape(seg.departure_airport)} / "
            f"{escape(seg.arrival_airport)}</td>"
            f"<td>{escape(_time(seg.departure_at))}</td>"
            f"<td>{escape(_time(seg.arrival_at))}</td>"
            "</tr>"
            for seg in option.segments
        )

        fares_html: list[str] = []

        for fare in option.fares:
            lines = _fare_lines(fare)
            title = escape(lines[0])
            details = "".join(
                f"<li>{escape(line)}</li>"
                for line in lines[1:]
            )
            fares_html.append(
                f"<div style='margin:12px 0'><strong>{title}</strong>"
                f"<ul>{details}</ul></div>"
            )

        option_html.append(
            f"<h3>Opción {option.display_number}</h3>"
            "<table style='border-collapse:collapse;width:100%' "
            "border='1' cellpadding='6'>"
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
        + f"<p><em>{escape(document.disclaimer)}</em></p>"
        f"<p>Referencia: {escape(document.quote_id)}</p>"
        "</body></html>"
    )


def render_whatsapp(record: StoredQuoteRecord) -> str:
    return render_whatsapp_document(
        build_commercial_quote(record)
    )


def render_email_html(record: StoredQuoteRecord) -> str:
    return render_email_document(
        build_commercial_quote(record)
    )


def render_stored_quote(
    record: StoredQuoteRecord,
    format: str,
) -> QuoteRenderResponse:
    document = build_commercial_quote(record)

    if format == "whatsapp":
        content = render_whatsapp_document(document)
        content_type = "text/plain; charset=utf-8"
    elif format == "email":
        content = render_email_document(document)
        content_type = "text/html; charset=utf-8"
    else:
        raise ValueError(
            "Formato no soportado. Usá whatsapp o email."
        )

    return QuoteRenderResponse(
        quote_id=record.quote_id,
        format=format,
        selected_ranks=record.selected_ranks,
        content_type=content_type,
        content=content,
    )
