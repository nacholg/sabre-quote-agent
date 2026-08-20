from __future__ import annotations

import re

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.models.itinerary import (
    BrandFeature,
    BrandedComponent,
    FareOption,
    FlightSegment,
    ItineraryOption,
    TaxDetail,
)
from app.services.pricing_rules import pricing_modifier


class UnsupportedSabreResponse(ValueError):
    pass


CABIN_NAMES = {"Y": "economy", "S": "premium economy", "C": "business", "F": "first"}


def _index_by_id(items: list[dict[str, Any]] | None) -> dict[int, dict[str, Any]]:
    return {int(item["id"]): item for item in (items or []) if "id" in item}


def _local_datetime(date_value: str, time_value: str) -> datetime:
    return datetime.fromisoformat(f"{date_value}T{time_value}")


def _arrival_datetime(departure: datetime, arrival_time: str, elapsed_minutes: int | None) -> datetime:
    candidate = _local_datetime(departure.date().isoformat(), arrival_time)
    if elapsed_minutes is not None:
        expected = departure + timedelta(minutes=elapsed_minutes)
        possibilities = [candidate + timedelta(days=days) for days in range(0, 4)]
        return min(possibilities, key=lambda value: abs((value - expected).total_seconds()))
    while candidate <= departure:
        candidate += timedelta(days=1)
    return candidate


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def itinerary_signature(option: ItineraryOption) -> tuple:
    return tuple(
        (
            segment.marketing_carrier,
            segment.flight_number,
            segment.departure_airport,
            segment.arrival_airport,
            segment.departure_at.isoformat(),
            segment.arrival_at.isoformat(),
        )
        for segment in option.segments
    )


def merge_currency_itineraries(
    primary: list[ItineraryOption], secondary: list[ItineraryOption]
) -> list[ItineraryOption]:
    secondary_by_key = {itinerary_signature(item): item for item in secondary}
    merged: list[ItineraryOption] = []
    seen: set[tuple] = set()

    for item in primary:
        key = itinerary_signature(item)
        fares = dict(item.fares_by_currency or {item.fare.currency: item.fare})
        fare_options = dict(item.fare_options_by_currency)
        if not fare_options:
            fare_options[item.fare.currency] = [item.fare]
        match = secondary_by_key.get(key)
        if match:
            fares.update(match.fares_by_currency or {match.fare.currency: match.fare})
            match_options = dict(match.fare_options_by_currency)
            if not match_options:
                match_options[match.fare.currency] = [match.fare]
            fare_options.update(match_options)
        item.fares_by_currency = fares
        item.fare_options_by_currency = fare_options
        merged.append(item)
        seen.add(key)

    for item in secondary:
        key = itinerary_signature(item)
        if key in seen:
            continue
        if not item.fares_by_currency:
            item.fares_by_currency = {item.fare.currency: item.fare}
        if not item.fare_options_by_currency:
            item.fare_options_by_currency = {item.fare.currency: [item.fare]}
        merged.append(item)

    return merged


def merge_cabin_itineraries(
    primary: list[ItineraryOption],
    companion: list[ItineraryOption],
    *,
    cabins: set[str] | None = None,
    include_unmatched: bool = False,
) -> list[ItineraryOption]:
    """Attach fare options from a companion cabin search to exact matching flights.

    Segment signatures must match exactly, so a Business fare from a different
    routing is never mixed into the customer's displayed itinerary.
    """
    wanted = {c.lower() for c in cabins} if cabins else None
    companion_by_key = {itinerary_signature(item): item for item in companion}
    matched_keys: set[tuple] = set()

    for item in primary:
        item_key = itinerary_signature(item)
        match = companion_by_key.get(item_key)
        if not match:
            continue
        matched_keys.add(item_key)
        for currency, fares in match.fare_options_by_currency.items():
            selected = [
                fare for fare in fares
                if wanted is None or fare.cabin.lower() in wanted
            ]
            if not selected:
                continue
            existing = item.fare_options_by_currency.setdefault(currency, [])
            seen = {
                (fare.cabin.lower(), fare.brand_name or '', fare.price_per_passenger)
                for fare in existing
            }
            for fare in selected:
                key = (fare.cabin.lower(), fare.brand_name or '', fare.price_per_passenger)
                if key not in seen:
                    existing.append(fare)
                    seen.add(key)

    if include_unmatched:
        for item in companion:
            key = itinerary_signature(item)
            if key in matched_keys or any(itinerary_signature(existing) == key for existing in primary):
                continue
            if not item.fares_by_currency:
                item.fares_by_currency = {item.fare.currency: item.fare}
            if not item.fare_options_by_currency:
                item.fare_options_by_currency = {item.fare.currency: [item.fare]}
            primary.append(item)

    return primary


def _build_segments(
    itinerary: dict[str, Any],
    descriptions: list[dict[str, Any]],
    schedules: dict[int, dict[str, Any]],
    legs: dict[int, dict[str, Any]],
    booking_segments: list[dict[str, Any]],
) -> list[FlightSegment]:
    segments: list[FlightSegment] = []
    booking_cursor = 0
    for leg_position, itinerary_leg in enumerate(itinerary.get("legs") or []):
        leg_ref = itinerary_leg.get("ref")
        leg = legs.get(int(leg_ref)) if leg_ref is not None else None
        if not leg:
            continue
        departure_date = descriptions[leg_position].get("departureDate") if leg_position < len(descriptions) else None
        if not departure_date:
            raise UnsupportedSabreResponse("No se encontró departureDate para una pierna")

        current_date = departure_date
        previous_arrival: datetime | None = None
        for schedule_ref_entry in leg.get("schedules", []):
            schedule_ref = schedule_ref_entry.get("ref")
            schedule = schedules.get(int(schedule_ref)) if schedule_ref is not None else None
            if not schedule:
                continue
            departure_data = schedule.get("departure") or {}
            arrival_data = schedule.get("arrival") or {}
            departure_at = _local_datetime(current_date, departure_data["time"])
            if previous_arrival is not None:
                while departure_at <= previous_arrival:
                    departure_at += timedelta(days=1)
            arrival_at = _arrival_datetime(departure_at, arrival_data["time"], schedule.get("elapsedTime"))
            previous_arrival = arrival_at
            current_date = arrival_at.date().isoformat()

            booking = booking_segments[booking_cursor] if booking_cursor < len(booking_segments) else {}
            booking_cursor += 1
            carrier = schedule.get("carrier") or {}
            segments.append(
                FlightSegment(
                    marketing_carrier=carrier.get("marketing", ""),
                    operating_carrier=carrier.get("operating"),
                    flight_number=str(carrier.get("marketingFlightNumber", "")),
                    departure_airport=departure_data.get("airport", ""),
                    arrival_airport=arrival_data.get("airport", ""),
                    departure_country=departure_data.get("country"),
                    arrival_country=arrival_data.get("country"),
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    booking_class=booking.get("bookingCode"),
                    cabin_code=booking.get("cabinCode"),
                    seats_available=booking.get("seatsAvailable"),
                )
            )
    return segments


def _brand_feature(entry: dict[str, Any]) -> BrandFeature:
    return BrandFeature(
        application=entry.get("application", ""),
        commercial_name=entry.get("commercialName", ""),
        service_type=entry.get("serviceType"),
        service_group=entry.get("serviceGroup"),
        sub_code=entry.get("subCode"),
    )


def _normalize_fare(
    pricing: dict[str, Any],
    fare_components: dict[int, dict[str, Any]],
    brand_features: dict[int, dict[str, Any]],
    baggage_allowances: dict[int, dict[str, Any]],
    tax_descs: dict[int, dict[str, Any]],
) -> tuple[FareOption, list[dict[str, Any]]]:
    fare = pricing.get("fare") or {}
    passenger_info_list = fare.get("passengerInfoList") or []
    if not passenger_info_list:
        raise UnsupportedSabreResponse("pricingInformation no contiene passengerInfoList")
    passenger_info = passenger_info_list[0].get("passengerInfo") or {}

    booking_segments: list[dict[str, Any]] = []
    fare_basis_codes: list[str] = []
    cabin_codes: list[str] = []
    branded_components: list[BrandedComponent] = []
    all_features: list[BrandFeature] = []
    seen_features: set[tuple[str, str, str | None]] = set()
    brand_codes: list[str] = []
    brand_names: list[str] = []

    for component_entry in passenger_info.get("fareComponents") or []:
        component_ref = component_entry.get("ref")
        component = fare_components.get(int(component_ref)) if component_ref is not None else None
        if component:
            fare_basis = component.get("fareBasisCode")
            if fare_basis and fare_basis not in fare_basis_codes:
                fare_basis_codes.append(fare_basis)
            cabin = component.get("cabinCode")
            if cabin:
                cabin_codes.append(cabin)
            brand = component.get("brand") or {}
            if brand.get("code") and brand["code"] not in brand_codes:
                brand_codes.append(brand["code"])
            if brand.get("brandName") and brand["brandName"] not in brand_names:
                brand_names.append(brand["brandName"])

            component_features: list[BrandFeature] = []
            for feature_ref in component_entry.get("brandFeatures", []):
                ref = feature_ref.get("ref")
                raw_feature = brand_features.get(int(ref)) if ref is not None else None
                if not raw_feature:
                    continue
                feature = _brand_feature(raw_feature)
                component_features.append(feature)
                key = (feature.application, feature.commercial_name, feature.sub_code)
                if key not in seen_features:
                    seen_features.add(key)
                    all_features.append(feature)

            branded_components.append(
                BrandedComponent(
                    component_ref=int(component_ref) if component_ref is not None else None,
                    begin_airport=component_entry.get("beginAirport"),
                    end_airport=component_entry.get("endAirport"),
                    fare_basis_code=fare_basis,
                    governing_carrier=(
                        component.get("governingCarrier")
                        or component.get("carrier")
                        or component.get("marketingCarrier")
                    ),
                    vendor_code=component.get("vendorCode") or component.get("vendor"),
                    tariff=str(component.get("tariff")) if component.get("tariff") is not None else None,
                    rule_number=component.get("ruleNumber") or component.get("rule"),
                    fare_amount=(
                        Decimal(str(component.get("amount")))
                        if component.get("amount") is not None
                        else None
                    ),
                    fare_currency=component.get("currency"),
                    brand_code=brand.get("code"),
                    brand_name=brand.get("brandName"),
                    program_code=brand.get("programCode"),
                    features=component_features,
                )
            )
        booking_segments.extend(item.get("segment", {}) for item in component_entry.get("segments", []))

    passenger_total = passenger_info.get("passengerTotalFare") or {}
    total_fare = fare.get("totalFare") or {}

    from app.models.itinerary import PassengerPrice

    raw_passenger_prices: list[
        tuple[str, int, int | None, dict[str, Any], Decimal]
    ] = []

    for passenger_entry in passenger_info_list:
        info = passenger_entry.get("passengerInfo") or {}

        ptc = str(
            info.get("passengerType")
            or info.get("passengerTypeCode")
            or info.get("requestedPassengerType")
            or passenger_entry.get("passengerType")
            or "ADT"
        ).upper()

        quantity_raw = (
            info.get("passengerNumber")
            or info.get("passengerCount")
            or info.get("quantity")
            or passenger_entry.get("passengerNumber")
            or passenger_entry.get("quantity")
            or 1
        )
        try:
            quantity = max(1, int(quantity_raw))
        except (TypeError, ValueError):
            quantity = 1

        age = None
        age_match = re.fullmatch(r"C(\d{1,2})", ptc)
        if age_match:
            age = int(age_match.group(1))

        ptc_total = info.get("passengerTotalFare") or {}
        raw_total = _decimal(ptc_total.get("totalFare"))
        if raw_total is None:
            continue

        raw_passenger_prices.append(
            (ptc, quantity, age, ptc_total, raw_total)
        )

    global_total = _decimal(total_fare.get("totalPrice"))
    passenger_prices: list[PassengerPrice] = []

    group_total_mode = False
    if raw_passenger_prices and global_total is not None:
        summed_raw = sum(
            (row[4] for row in raw_passenger_prices),
            Decimal("0"),
        )
        summed_unit_times_qty = sum(
            (row[4] * row[1] for row in raw_passenger_prices),
            Decimal("0"),
        )
        group_total_mode = (
            abs(summed_raw - global_total)
            < abs(summed_unit_times_qty - global_total)
        )

    for ptc, quantity, age, ptc_total, raw_total in raw_passenger_prices:
        if group_total_mode and quantity > 0:
            unit_price = raw_total / Decimal(quantity)
            ptc_group_total = raw_total
        else:
            unit_price = raw_total
            ptc_group_total = raw_total * Decimal(quantity)

        ptc_currency = (
            ptc_total.get("currency")
            or total_fare.get("currency")
            or "USD"
        )

        passenger_prices.append(
            PassengerPrice(
                passenger_type=ptc,
                quantity=quantity,
                age=age,
                currency=ptc_currency,
                unit_price=unit_price,
                total_price=ptc_group_total,
                total_tax=_decimal(
                    ptc_total.get("totalTaxAmount")
                ),
                base_fare_amount=_decimal(
                    ptc_total.get("baseFareAmount")
                ),
            )
        )

    conversion = passenger_info.get("currencyConversion") or {}
    currency = passenger_total.get("currency") or total_fare.get("currency") or "USD"
    price_per_passenger = Decimal(str(passenger_total.get("totalFare", total_fare.get("totalPrice", 0))))
    total_price = Decimal(str(total_fare.get("totalPrice", price_per_passenger)))
    total_tax = Decimal(str(passenger_total.get("totalTaxAmount", 0)))

    taxes: list[TaxDetail] = []
    q1_amount = Decimal("0")
    q1_currency: str | None = None
    found_q1 = False
    for tax_ref_entry in passenger_info.get("taxes", []):
        tax_ref = tax_ref_entry.get("ref")
        if tax_ref is None:
            continue
        tax = tax_descs.get(int(tax_ref))
        if not tax:
            continue
        detail = TaxDetail(
            code=tax.get("code", ""),
            amount=Decimal(str(tax.get("amount", 0))),
            currency=tax.get("currency") or currency,
            description=tax.get("description"),
            station=tax.get("station"),
            country=tax.get("country"),
        )
        taxes.append(detail)
        if detail.code == "Q1":
            found_q1 = True
            q1_amount += detail.amount
            q1_currency = detail.currency

    baggage_piece_counts: list[int] = []
    baggage_text: list[str] = []
    allowance_descriptions: list[str] = []
    for baggage_info in passenger_info.get("baggageInformation", []):
        if baggage_info.get("provisionType") not in {None, "A"}:
            continue
        allowance_ref = (baggage_info.get("allowance") or {}).get("ref")
        if allowance_ref is None:
            continue
        allowance = baggage_allowances.get(int(allowance_ref))
        if not allowance:
            continue
        if allowance.get("pieceCount") is not None:
            baggage_piece_counts.append(int(allowance["pieceCount"]))
        for key in ("description1", "description2"):
            text = allowance.get(key)
            if text and text not in allowance_descriptions:
                allowance_descriptions.append(text)

    baggage_pieces = min(baggage_piece_counts) if baggage_piece_counts else None
    if baggage_pieces is not None:
        if baggage_pieces == 0:
            baggage_text.append("No incluye equipaje despachado.")
        elif baggage_pieces == 1:
            detail = " de hasta 23 kg" if any("23 KILOGRAMS" in x.upper() for x in allowance_descriptions) else ""
            baggage_text.append(f"1 pieza despachada{detail} por pasajero.")
        else:
            baggage_text.append(f"{baggage_pieces} piezas despachadas por pasajero.")

    cabin_code = next((code for code in cabin_codes if code), "Y")
    brand_code = " / ".join(brand_codes) if brand_codes else None
    brand_name = " / ".join(brand_names) if brand_names else None

    normalized = FareOption(
        cabin=CABIN_NAMES.get(cabin_code, cabin_code),
        cabin_codes=list(dict.fromkeys(cabin_codes)),
        currency=currency,
        price_per_passenger=price_per_passenger,
        total_price=total_price,
        passenger_prices=passenger_prices,
        total_tax=total_tax,
        base_fare_amount=_decimal(passenger_total.get("baseFareAmount")),
        base_fare_currency=passenger_total.get("baseFareCurrency"),
        equivalent_amount=_decimal(passenger_total.get("equivalentAmount")),
        equivalent_currency=passenger_total.get("equivalentCurrency"),
        exchange_rate=_decimal(conversion.get("exchangeRateUsed")),
        pricing_modifier=pricing_modifier(currency) if currency in {"USD", "ARS"} else None,
        taxes=taxes,
        q1_amount=q1_amount if found_q1 else None,
        q1_currency=q1_currency,
        fare_basis_codes=fare_basis_codes,
        validating_carrier=fare.get("validatingCarrierCode"),
        non_refundable=passenger_info.get("nonRefundable"),
        last_ticket_date=fare.get("lastTicketDate"),
        baggage_pieces=baggage_pieces,
        baggage=baggage_text,
        brand_code=brand_code,
        brand_name=brand_name,
        branded_components=branded_components,
        brand_features=all_features,
    )
    return normalized, booking_segments


def normalize_bfm_response(payload: dict[str, Any]) -> list[ItineraryOption]:
    root = payload.get("groupedItineraryResponse")
    if not isinstance(root, dict):
        raise UnsupportedSabreResponse("La respuesta no contiene groupedItineraryResponse de BFM v5")

    schedules = _index_by_id(root.get("scheduleDescs"))
    legs = _index_by_id(root.get("legDescs"))
    fare_components = _index_by_id(root.get("fareComponentDescs"))
    brand_features = _index_by_id(root.get("brandFeatureDescs"))
    baggage_allowances = _index_by_id(root.get("baggageAllowanceDescs"))
    tax_descs = _index_by_id(root.get("taxDescs"))

    results: list[ItineraryOption] = []
    source_index = 0

    for group in root.get("itineraryGroups", []):
        descriptions = group.get("groupDescription", {}).get("legDescriptions", [])
        for itinerary in group.get("itineraries", []):
            fare_options: list[FareOption] = []
            first_booking_segments: list[dict[str, Any]] | None = None
            for pricing in itinerary.get("pricingInformation") or []:
                # Sold-out branded rows contain no fare and must not be presented as purchasable.
                if not pricing.get("fare"):
                    continue
                try:
                    normalized_fare, booking_segments = _normalize_fare(
                        pricing,
                        fare_components,
                        brand_features,
                        baggage_allowances,
                        tax_descs,
                    )
                except UnsupportedSabreResponse:
                    continue
                fare_options.append(normalized_fare)
                if first_booking_segments is None:
                    first_booking_segments = booking_segments

            if not fare_options or first_booking_segments is None:
                continue

            # Keep Sabre price-point order; first fare is the primary/lowest option.
            primary_fare = fare_options[0]
            currency = primary_fare.currency
            segments = _build_segments(itinerary, descriptions, schedules, legs, first_booking_segments)
            option = ItineraryOption(
                segments=segments,
                fare=primary_fare,
                fares_by_currency={currency: primary_fare},
                fare_options_by_currency={currency: fare_options},
                source_index=source_index,
            )
            results.append(option)
            source_index += 1

    return results
