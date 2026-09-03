from __future__ import annotations

from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from app.models.pnr_workspace import (
    PnrContact,
    PnrPassenger,
    PnrPriceQuote,
    PnrSegment,
    PnrSnapshot,
    PnrSpecialService,
    PnrTicketing,
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(node) if _local(child.tag) == name]


def _first_child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    for child in list(node):
        if _local(child.tag) == name:
            return child
    return None


def _first_descendant(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    for child in node.iter():
        if child is node:
            continue
        if _local(child.tag) == name:
            return child
    return None


def _descendants(node: ET.Element | None, name: str) -> list[ET.Element]:
    if node is None:
        return []
    return [
        child
        for child in node.iter()
        if child is not node and _local(child.tag) == name
    ]


def _text(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    value = (node.text or "").strip()
    return value or None


def _attr(node: ET.Element | None, *names: str) -> str | None:
    if node is None:
        return None
    for name in names:
        value = (node.attrib.get(name) or "").strip()
        if value:
            return value
    return None


def _bool_attr(node: ET.Element | None, *names: str) -> bool | None:
    value = _attr(node, *names)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "y", "1"}:
        return True
    if normalized in {"false", "no", "n", "0"}:
        return False
    return None


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _normalize_flight_number(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.isdigit():
        return str(int(normalized))
    return normalized


def _normalize_sabre_datetime(
    value: str | None,
    *,
    reference: str | None = None,
) -> str | None:
    """Expand Sabre MM-DD arrival timestamps using departure year.

    TravelItineraryReadRS can return a full departure timestamp but a partial
    arrival timestamp such as ``09-20T04:55``. Preserve full timestamps as-is
    and only synthesize the year when the reference departure carries one.
    A month/day lower than the departure month/day is treated as year rollover.
    """

    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) >= 10 and normalized[4] == "-" and normalized[7] == "-":
        return normalized
    if (
        len(normalized) >= 6
        and normalized[2] == "-"
        and normalized[5] == "T"
        and reference
        and len(reference) >= 10
        and reference[4] == "-"
        and reference[7] == "-"
    ):
        year = int(reference[:4])
        departure_month_day = reference[5:10]
        arrival_month_day = normalized[:5]
        if arrival_month_day < departure_month_day:
            year += 1
        return f"{year:04d}-{normalized}"
    return normalized


def _find_travel_itinerary(root: ET.Element) -> ET.Element | None:
    if _local(root.tag) == "TravelItinerary":
        return root
    return _first_descendant(root, "TravelItinerary")


def _unique(values: list[str | None]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _parse_passengers(customer_info: ET.Element | None) -> list[PnrPassenger]:
    result: list[PnrPassenger] = []
    for node in _children(customer_info, "PersonName") if customer_info is not None else []:
        name_number = _attr(node, "NameNumber")
        if not name_number:
            continue
        emails = _unique([_text(email) for email in _children(node, "Email")])
        result.append(
            PnrPassenger(
                name_number=name_number,
                rph=_attr(node, "RPH"),
                passenger_type=_attr(node, "PassengerType"),
                given_name=_text(_first_child(node, "GivenName")),
                surname=_text(_first_child(node, "Surname")),
                with_infant=_bool_attr(node, "WithInfant"),
                emails=emails,
            )
        )
    return result


def _parse_contacts(customer_info: ET.Element | None) -> list[PnrContact]:
    if customer_info is None:
        return []

    result: list[PnrContact] = []
    contact_numbers = _first_child(customer_info, "ContactNumbers")
    if contact_numbers is not None:
        for node in _children(contact_numbers, "ContactNumber"):
            phone = _attr(node, "Phone")
            if not phone:
                continue
            result.append(
                PnrContact(
                    kind="phone",
                    value=phone,
                    name_number=_attr(node, "NameNumber"),
                    usage_type=_attr(node, "PhoneUseType", "Type"),
                    location_code=_attr(node, "LocationCode"),
                )
            )

    for person in _children(customer_info, "PersonName"):
        name_number = _attr(person, "NameNumber")
        for email in _children(person, "Email"):
            value = _text(email) or _attr(email, "Address")
            if not value:
                continue
            result.append(
                PnrContact(
                    kind="email",
                    value=value,
                    name_number=name_number,
                    usage_type=_attr(email, "Type"),
                    comment=_attr(email, "Comment"),
                )
            )

    # Some Sabre payload variants place Email directly under CustomerInfo.
    for email in _children(customer_info, "Email"):
        value = _text(email) or _attr(email, "Address")
        if not value:
            continue
        result.append(
            PnrContact(
                kind="email",
                value=value,
                name_number=_attr(email, "NameNumber"),
                usage_type=_attr(email, "Type"),
                comment=_attr(email, "Comment"),
            )
        )

    deduped: list[PnrContact] = []
    seen: set[tuple[str, str, str | None]] = set()
    for contact in result:
        key = (contact.kind, contact.value, contact.name_number)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(contact)
    return deduped


def _parse_reservation_segments(itinerary_info: ET.Element | None) -> list[PnrSegment]:
    reservation_items = _first_child(itinerary_info, "ReservationItems")
    if reservation_items is None:
        return []

    result: list[PnrSegment] = []
    for item in _children(reservation_items, "Item"):
        flight = _first_child(item, "FlightSegment")
        if flight is None:
            # Tolerate a product wrapper without accidentally reading pricing
            # FlightSegment nodes elsewhere in the response.
            flight = _first_descendant(item, "FlightSegment")
        if flight is None:
            continue

        marketing = _first_child(flight, "MarketingAirline")
        operating = _first_child(flight, "OperatingAirline")
        origin = _first_child(flight, "OriginLocation")
        destination = _first_child(flight, "DestinationLocation")
        supplier = _first_child(flight, "SupplierRef")

        departure_at = _normalize_sabre_datetime(
            _attr(flight, "DepartureDateTime")
        )
        arrival_at = _normalize_sabre_datetime(
            _attr(flight, "ArrivalDateTime"),
            reference=departure_at,
        )

        result.append(
            PnrSegment(
                segment_number=_attr(flight, "SegmentNumber")
                or _attr(item, "RPH"),
                rph=_attr(item, "RPH") or _attr(flight, "RPH"),
                marketing_carrier=_attr(marketing, "Code"),
                operating_carrier=_attr(operating, "Code"),
                flight_number=_normalize_flight_number(
                    _attr(flight, "FlightNumber")
                    or _attr(marketing, "FlightNumber")
                ),
                origin=_attr(origin, "LocationCode"),
                destination=_attr(destination, "LocationCode"),
                departure_at=departure_at,
                arrival_at=arrival_at,
                booking_class=_attr(flight, "ResBookDesigCode"),
                status=_attr(flight, "Status"),
                number_in_party=_int(_attr(flight, "NumberInParty")),
                airline_locator=_attr(supplier, "ID"),
                e_ticket=_bool_attr(flight, "eTicket", "ETicket"),
            )
        )
    return result


def _sum_tax_amount(taxes: ET.Element | None) -> Decimal | None:
    if taxes is None:
        return None
    values = [
        _decimal(_attr(node, "Amount"))
        for node in _children(taxes, "Tax")
    ]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present, Decimal("0"))


def _parse_price_quotes(itinerary_info: ET.Element | None) -> list[PnrPriceQuote]:
    itinerary_pricing = _first_child(itinerary_info, "ItineraryPricing")
    if itinerary_pricing is None:
        return []

    result: list[PnrPriceQuote] = []
    for price_quote in _children(itinerary_pricing, "PriceQuote"):
        signature = _first_descendant(
            _first_child(price_quote, "MiscInformation"),
            "SignatureLine",
        )
        priced = _first_child(price_quote, "PricedItinerary")
        pricing_info = _first_child(priced, "AirItineraryPricingInfo")
        itin_total = _first_child(pricing_info, "ItinTotalFare")
        base_fare = _first_child(itin_total, "BaseFare")
        equiv_fare = _first_child(itin_total, "EquivFare")
        taxes = _first_child(itin_total, "Taxes")
        total_fare = _first_child(itin_total, "TotalFare")
        totals = _first_child(itin_total, "Totals")
        totals_total = _first_child(totals, "TotalFare")

        ptq = _first_child(pricing_info, "PassengerTypeQuantity")
        breakdown = _first_child(pricing_info, "PTC_FareBreakdown")
        fare_basis_node = _first_child(breakdown, "FareBasis")
        fare_basis = _attr(fare_basis_node, "Code")
        pricing_segments = (
            _children(breakdown, "FlightSegment")
            if breakdown is not None
            else []
        )
        fare_basis_codes = _unique(
            [
                _attr(_first_child(segment, "FareBasis"), "Code")
                for segment in pricing_segments
            ]
            + [
                _attr(component, "FareBasisCode")
                for component in (
                    _children(breakdown, "FareComponent")
                    if breakdown is not None
                    else []
                )
            ]
        )
        if not fare_basis_codes and fare_basis:
            fare_basis_codes = [fare_basis]

        segment_booking_classes = _unique(
            [
                _attr(segment, "ResBookDesigCode")
                for segment in pricing_segments
            ]
        )

        passenger_info = _first_descendant(
            _first_child(price_quote, "PriceQuotePlus"),
            "PassengerInfo",
        )
        passenger_name_numbers = _unique(
            [
                _attr(data, "NameNumber")
                for data in _children(passenger_info, "PassengerData")
            ]
            if passenger_info is not None
            else []
        )

        total_currency = _attr(total_fare, "CurrencyCode")
        total_amount = _decimal(_attr(totals_total, "Amount"))
        if total_amount is None:
            total_amount = _decimal(_attr(total_fare, "Amount"))

        result.append(
            PnrPriceQuote(
                record_number=_attr(priced, "RPH")
                or _attr(price_quote, "RPH"),
                status=_attr(signature, "Status"),
                stored_at=_attr(priced, "StoredDateTime"),
                validating_carrier=_attr(priced, "ValidatingCarrier"),
                passenger_type=_attr(ptq, "Code")
                or _attr(passenger_info, "PassengerType"),
                passenger_quantity=_int(_attr(ptq, "Quantity")),
                passenger_name_numbers=passenger_name_numbers,
                base_fare_amount=_decimal(_attr(base_fare, "Amount")),
                base_fare_currency=_attr(base_fare, "CurrencyCode"),
                equivalent_fare_amount=_decimal(_attr(equiv_fare, "Amount")),
                equivalent_fare_currency=_attr(equiv_fare, "CurrencyCode"),
                per_passenger_tax_amount=_sum_tax_amount(taxes),
                per_passenger_total_amount=_decimal(
                    _attr(total_fare, "Amount")
                ),
                total_amount=total_amount,
                total_currency=total_currency
                or _attr(equiv_fare, "CurrencyCode")
                or _attr(base_fare, "CurrencyCode"),
                fare_basis=fare_basis,
                fare_basis_codes=fare_basis_codes,
                segment_booking_classes=segment_booking_classes,
            )
        )
    return result


def _ticketing_arrangement_type(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return None
    for code in ("TAW", "TAX", "TAU", "T-A"):
        if normalized.startswith(code):
            return code
    return None


def _parse_ticketing(
    travel_itinerary: ET.Element,
    special_services: list[PnrSpecialService],
) -> PnrTicketing:
    itinerary_info = _first_child(travel_itinerary, "ItineraryInfo")
    itinerary_ticketing = _first_child(itinerary_info, "Ticketing")

    # Older/alternate TIR payloads and our historical fixtures may also expose
    # Ticketing under AgencyInfo. ItineraryInfo is authoritative for the
    # actual TIR 3.10 shape observed in CERT; AgencyInfo remains a fallback.
    agency_info = _first_child(travel_itinerary, "AgencyInfo")
    agency_ticketing = _first_child(agency_info, "Ticketing")
    ticketing = (
        itinerary_ticketing
        if itinerary_ticketing is not None
        else agency_ticketing
    )

    arrangement_raw = _attr(itinerary_ticketing, "TicketTimeLimit")
    if arrangement_raw is None:
        arrangement_raw = _attr(agency_ticketing, "TicketTimeLimit")

    ticket_type = _attr(ticketing, "TicketType")
    if ticket_type is None:
        ticket_type = _attr(agency_ticketing, "TicketType")

    ticketing_text = _text(ticketing)
    if ticketing_text is None:
        ticketing_text = _text(agency_ticketing)

    advisory = next(
        (service for service in special_services if service.code == "ADTK"),
        None,
    )

    return PnrTicketing(
        ticket_type=ticket_type,
        ticketing_text=ticketing_text,
        arrangement_raw=arrangement_raw,
        arrangement_type=_ticketing_arrangement_type(arrangement_raw),
        arrangement_rph=_attr(itinerary_ticketing, "RPH")
        or _attr(agency_ticketing, "RPH"),
        advisory_present=advisory is not None,
        advisory_code=advisory.code if advisory is not None else None,
        advisory_status=advisory.status if advisory is not None else None,
        advisory_airline_code=(
            advisory.airline_code if advisory is not None else None
        ),
        # TicketTimeLimit can contain a Sabre ticketing-arrangement value such
        # as TAW/. It is not safe to reinterpret that agency workflow field as
        # an airline-imposed ticketing deadline. ADTK free text is likewise
        # deliberately not parsed here.
        deadline_at=None,
    )


def _association_values(
    service_request: ET.Element,
    *,
    association_names: set[str],
    attribute_names: tuple[str, ...],
) -> list[str]:
    values: list[str | None] = []
    parent = service_request
    # Associations can be siblings under OpenReservationElement rather than
    # children of ServiceRequest, so the caller passes the wrapper when needed.
    for node in parent.iter():
        if _local(node.tag) not in association_names:
            continue
        values.append(_attr(node, *attribute_names))
        values.append(_text(node))
    return _unique(values)


def _parse_special_services(travel_itinerary: ET.Element) -> list[PnrSpecialService]:
    open_elements = _first_child(travel_itinerary, "OpenReservationElements")
    if open_elements is None:
        return []

    result: list[PnrSpecialService] = []
    for wrapper in _children(open_elements, "OpenReservationElement"):
        service = _first_descendant(wrapper, "ServiceRequest")
        if service is None:
            continue
        code = _attr(service, "code", "Code")
        if not code:
            continue

        name_numbers = _association_values(
            wrapper,
            association_names={
                "NameAssociation",
                "NameRefNumber",
                "NameNumber",
            },
            attribute_names=("NameNumber", "nameNumber", "Reference"),
        )
        segment_numbers = _association_values(
            wrapper,
            association_names={
                "SegmentAssociation",
                "SegmentRefNumber",
                "SegmentNumber",
            },
            attribute_names=("SegmentNumber", "segmentNumber", "Reference"),
        )

        result.append(
            PnrSpecialService(
                code=code.upper(),
                status=_attr(service, "actionCode", "ActionCode", "Status"),
                airline_code=_attr(
                    service,
                    "airlineCode",
                    "AirlineCode",
                ),
                service_type=_attr(
                    service,
                    "serviceType",
                    "ServiceType",
                ),
                name_numbers=name_numbers,
                segment_numbers=segment_numbers,
            )
        )
    return result


def parse_pnr_snapshot(
    root: ET.Element,
    *,
    confirmation_id: str,
    application_status: str,
) -> PnrSnapshot:
    """Normalize the read-only TIR response into an application-owned model.

    Parsing is namespace-insensitive and intentionally tolerant of absent
    optional Sabre fields. The returned snapshot excludes raw SSR free text and
    payment data; v0.34 only needs operational presence/status signals.
    """

    travel_itinerary = _find_travel_itinerary(root)
    if travel_itinerary is None:
        raise ValueError("TravelItineraryReadRS sin TravelItinerary.")

    customer_info = _first_child(travel_itinerary, "CustomerInfo")
    itinerary_info = _first_child(travel_itinerary, "ItineraryInfo")

    special_services = _parse_special_services(travel_itinerary)

    return PnrSnapshot(
        confirmation_id=confirmation_id.strip().upper(),
        application_status=application_status,
        passengers=_parse_passengers(customer_info),
        segments=_parse_reservation_segments(itinerary_info),
        contacts=_parse_contacts(customer_info),
        price_quotes=_parse_price_quotes(itinerary_info),
        ticketing=_parse_ticketing(travel_itinerary, special_services),
        special_services=special_services,
    )
