from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from app.models.itinerary import FareOption, ItineraryOption
from app.services.ranking import RankedItinerary

AIRLINES = {
    "AA": "American Airlines", "AR": "Aerolíneas Argentinas", "AV": "Avianca",
    "CM": "Copa Airlines", "DL": "Delta Air Lines", "DM": "Arajet",
    "IB": "Iberia", "LA": "LATAM Airlines", "LO": "LOT Polish Airlines",
    "OB": "Boliviana de Aviación", "UA": "United Airlines",
}

AIRPORTS = {
    "EZE": "Ezeiza", "AEP": "Aeroparque Jorge Newbery", "MIA": "Miami, FL",
    "JFK": "Nueva York, NY", "DFW": "Dallas, TX", "PTY": "Panamá",
    "SDQ": "Santo Domingo", "PUJ": "Punta Cana", "SCL": "Santiago de Chile",
    "LIM": "Lima", "BOG": "Bogotá", "GRU": "São Paulo", "GIG": "Río de Janeiro",
    "VVI": "Santa Cruz de la Sierra", "ATL": "Atlanta, GA", "DTW": "Detroit, MI",
    "WAW": "Varsovia", "SPU": "Split", "COR": "Córdoba", "MDZ": "Mendoza",
    "BRC": "Bariloche",
}

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _date(value) -> str:
    return f"{value.day:02d}{MONTHS[value.month - 1]}"


def _time(value) -> str:
    return value.strftime("%H%M")


def _money(value: Decimal, currency: str) -> str:
    return f"{value:,.2f}"


def _ordered_fares(option: ItineraryOption) -> list[FareOption]:
    fares = option.fares_by_currency or {option.fare.currency: option.fare}
    ordered: list[FareOption] = []
    for currency in ("USD", "ARS"):
        if currency in fares:
            ordered.append(fares[currency])
    for currency, fare in fares.items():
        if currency not in {"USD", "ARS"}:
            ordered.append(fare)
    return ordered


def _brand_feature_status(fare: FareOption, keywords: tuple[str, ...]) -> str | None:
    matches = [
        feature for feature in fare.brand_features
        if any(keyword in feature.commercial_name.upper() for keyword in keywords)
    ]
    if not matches:
        return None
    statuses = {feature.application for feature in matches}
    if "F" in statuses:
        return "incluido"
    if "C" in statuses:
        return "con cargo"
    if statuses & {"N", "D"}:
        return "no incluido"
    return None


def _fare_baggage_line(fare: FareOption) -> str:
    if fare.baggage:
        return fare.baggage[0]
    status = _brand_feature_status(fare, ("FIRST BAG", "1ST CHECKED BAG", "FIRST CHECKED BAG"))
    if status == "incluido":
        return "Incluye primera pieza de equipaje despachado."
    if status in {"con cargo", "no incluido"}:
        return "No incluye equipaje despachado."
    return "Equipaje despachado sujeto a la tarifa seleccionada."


def _commercial_brand_features(fare: FareOption) -> list[str]:
    """Render only conditions supported by BFM, without upgrading absence into permission."""
    lines: list[str] = []
    seat = _brand_feature_status(fare, ("SEAT SELECTION", "SEAT RESERVATION"))
    changes = _brand_feature_status(fare, ("CHANGE BEFORE DEPARTURE", "CHANGE AFTER DEPARTURE"))
    refund = _brand_feature_status(fare, ("REFUND BEFORE DEPARTURE", "REFUND AFTER DEPARTURE"))
    priority = _brand_feature_status(fare, ("PRIORITY BOARDING",))

    if seat:
        lines.append(f"Selección de asiento: {seat}.")

    if changes == "incluido":
        lines.append("Cambios: permitidos sin cargo según atributo branded.")
    elif changes == "con cargo":
        lines.append("Cambios: permitidos con cargo según atributo branded.")
    elif changes == "no incluido":
        lines.append("Cambios: no permitidos según atributo branded.")
    else:
        lines.append("Cambios: confirmar reglas tarifarias.")

    if refund == "incluido":
        lines.append("Devoluciones: permitidas según atributo branded.")
    elif refund == "con cargo":
        lines.append("Devoluciones: permitidas con cargo según atributo branded.")
    elif refund == "no incluido":
        lines.append("Devoluciones: no permitidas según atributo branded.")
    elif fare.non_refundable is True:
        lines.append("Devoluciones: no permitidas.")
    else:
        # nonRefundable=false is not strong enough to claim a refund is permitted.
        lines.append("Devoluciones: confirmar reglas tarifarias.")

    if priority == "incluido":
        lines.append("Priority boarding incluido.")
    return lines


def _fare_ticketing_line(fare: FareOption, common_ticket_date: str | None) -> str | None:
    """Return ticketing deadline only when it is specific to this product."""
    if fare.last_ticket_date and fare.last_ticket_date != common_ticket_date:
        return f"Emitir hasta el {fare.last_ticket_date} o antes si cambia la disponibilidad."
    return None

def _render_single_fare(lines: list[str], fare: FareOption, option: ItineraryOption) -> None:
    lines.append(
        f"Tarifa por pasajero en {fare.cabin}: "
        f"{fare.currency} {_money(fare.price_per_passenger, fare.currency)}"
    )
    if fare.currency == "ARS" and not option.is_domestic_argentina and fare.q1_amount is not None:
        q1_currency = fare.q1_currency or "ARS"
        lines.append(f"Impuesto Q1 incluido: {q1_currency} {_money(fare.q1_amount, q1_currency)}")


def _select_commercial_fares(fare_options: list[FareOption]) -> list[FareOption]:
    """Return a compact commercial ladder: 2 Economy, 1 Premium, 1 Business.

    All Sabre brands remain in normalized JSON; this only controls client output.
    Economy Flexible is preferred as the second Economy product, which suppresses
    intermediate products such as AA Main Plus when a flexible brand exists.
    """
    available = sorted(fare_options, key=lambda fare: fare.price_per_passenger)
    economy = [fare for fare in available if fare.cabin.lower() == "economy"]
    premium = [fare for fare in available if fare.cabin.lower() == "premium economy"]
    business = [fare for fare in available if fare.cabin.lower() == "business"]

    selected: list[FareOption] = []
    if economy:
        lowest = economy[0]
        selected.append(lowest)
        remaining = [fare for fare in economy[1:] if fare is not lowest]
        flexible = [
            fare for fare in remaining
            if "FLEX" in (fare.brand_name or "").upper()
        ]
        second = min(flexible, key=lambda f: f.price_per_passenger) if flexible else (
            min(remaining, key=lambda f: f.price_per_passenger) if remaining else None
        )
        if second is not None:
            selected.append(second)
    if premium:
        selected.append(min(premium, key=lambda f: f.price_per_passenger))
    if business:
        selected.append(min(business, key=lambda f: f.price_per_passenger))
    return selected


def _render_branded_fares(lines: list[str], option: ItineraryOption) -> list[FareOption]:
    currencies = option.fare_options_by_currency or {}
    has_brand = any(fare.brand_name for items in currencies.values() for fare in items)
    if not has_brand:
        return []

    selected_by_currency: list[tuple[str, list[FareOption]]] = []
    all_selected: list[FareOption] = []
    for currency in ("USD", "ARS"):
        fare_options = currencies.get(currency) or []
        commercial = _select_commercial_fares(fare_options)
        if commercial:
            selected_by_currency.append((currency, commercial))
            all_selected.extend(commercial)

    ticket_dates = {fare.last_ticket_date for fare in all_selected if fare.last_ticket_date}
    common_ticket_date = next(iter(ticket_dates)) if len(ticket_dates) == 1 else None

    for _currency, commercial in selected_by_currency:
        for fare in commercial:
            brand_label = fare.brand_name or fare.brand_code or fare.cabin.upper()
            lines.append(f"{brand_label} — {fare.currency} {_money(fare.price_per_passenger, fare.currency)}")
            lines.append(f"Equipaje: {_fare_baggage_line(fare)}")
            if fare.currency == "ARS" and not option.is_domestic_argentina and fare.q1_amount is not None:
                q1_currency = fare.q1_currency or "ARS"
                lines.append(f"Q1 incluido: {q1_currency} {_money(fare.q1_amount, q1_currency)}")
            for feature_line in _commercial_brand_features(fare):
                lines.append(feature_line)
            ticketing = _fare_ticketing_line(fare, common_ticket_date)
            if ticketing:
                lines.append(ticketing)
            lines.append("")

    return all_selected

def _render_option(
    lines: list[str], option: ItineraryOption, option_index: int, recommended: bool,
    stops: int | None = None, duration_minutes: int | None = None,
) -> None:
    title = f"OPCIÓN {option_index}"
    if recommended:
        title += " — RECOMENDADA"
    lines.extend([title, ""])

    for segment_index, segment in enumerate(option.segments, start=1):
        arrival_date = _date(segment.arrival_at)
        departure_date = _date(segment.departure_at)
        suffix = f"  {arrival_date}" if arrival_date != departure_date else ""
        lines.append(
            f"{segment_index} {segment.marketing_carrier} {segment.flight_number:<5}  "
            f"{departure_date}  {segment.departure_airport}/{segment.arrival_airport}  "
            f"{_time(segment.departure_at)}  {_time(segment.arrival_at)}{suffix}"
        )

    lines.append("")
    rendered_brands = _render_branded_fares(lines, option)
    if not rendered_brands:
        for fare in _ordered_fares(option):
            _render_single_fare(lines, fare, option)

        lines.extend(["", "Equipaje"])
        lines.append(_fare_baggage_line(option.fare))
        lines.append("Equipaje de mano sujeto a las condiciones de la aerolínea.")

    lines.extend(["", "Condiciones"])
    if rendered_brands:
        # Refund/change conditions are product-specific and are rendered under
        # each branded fare. Only truly common conditions remain here.
        ticket_dates = {fare.last_ticket_date for fare in rendered_brands if fare.last_ticket_date}
        if len(ticket_dates) == 1:
            common_ticket_date = next(iter(ticket_dates))
            lines.append(f"Emitir hasta el {common_ticket_date} o antes si cambia la disponibilidad.")
    else:
        if option.fare.non_refundable is True:
            lines.append("Tarifa no reembolsable.")
        elif option.fare.non_refundable is False:
            lines.append("Tarifa reembolsable según las condiciones de la tarifa.")
        else:
            lines.append("Cambios y devoluciones sujetos a las reglas de la tarifa seleccionada.")
        if option.fare.last_ticket_date:
            lines.append(f"Emitir hasta el {option.fare.last_ticket_date} o antes si cambia la disponibilidad.")
    lines.append("Tarifas sujetas a disponibilidad hasta el momento de emisión." if rendered_brands else "Tarifa sujeta a disponibilidad hasta el momento de emisión.")
    lines.extend(["", ""])


def render_ranked_client_quote(ranked: list[RankedItinerary]) -> str:
    if not ranked:
        return "No se encontraron itinerarios disponibles para los criterios indicados.\n"

    lines: list[str] = []
    airline_codes: OrderedDict[str, None] = OrderedDict()
    airport_codes: OrderedDict[str, None] = OrderedDict()

    for item in ranked:
        option = item.option
        _render_option(lines, option, item.rank, recommended=item.rank == 1,
                       stops=item.stops, duration_minutes=item.duration_minutes)
        for segment in option.segments:
            airline_codes[segment.marketing_carrier] = None
            airport_codes[segment.departure_airport] = None
            airport_codes[segment.arrival_airport] = None

    lines.append("Referencias")
    for code in airline_codes:
        lines.append(f"{code}: {AIRLINES.get(code, code)}")
    for code in airport_codes:
        lines.append(f"{code}: {AIRPORTS.get(code, code)}")
    return "\n".join(lines).rstrip() + "\n"


def render_client_quote(options: list[ItineraryOption], include_option_headers: bool = True) -> str:
    if not options:
        return "No se encontraron itinerarios disponibles para los criterios indicados.\n"
    from app.services.ranking import rank_itineraries
    return render_ranked_client_quote(rank_itineraries(options, mode="price"))
