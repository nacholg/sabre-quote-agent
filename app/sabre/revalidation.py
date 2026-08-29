from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import get_settings
from app.models.booking import BookingOfferSnapshot
from app.models.itinerary import ItineraryOption
from app.models.quote_request import PassengerKind, PassengerSpec, SearchLeg
from app.sabre.client import SabreClient
from app.sabre.shopping import extract_bfm_diagnostics
from app.services.location_resolver import locations_equivalent
from app.services.normalizer import normalize_bfm_response


class RevalidationRequestError(ValueError):
    """Frozen Booking data cannot be represented as a Sabre revalidate request."""


@dataclass
class SabreRevalidationResult:
    options: list[ItineraryOption]
    transaction_id: str | None
    no_availability: bool
    messages: list[dict[str, Any]]


def _local_wall_clock(value: datetime) -> str:
    """Serialize Sabre local airport wall-clock time without timezone conversion."""
    return value.replace(tzinfo=None).isoformat(timespec="seconds")


def _flight_number(value: str) -> int:
    raw = str(value).strip()
    if not raw.isdigit():
        raise RevalidationRequestError(
            f"Número de vuelo inválido para revalidación: {value!r}."
        )
    return int(raw)


def _sabre_passenger_types(
    passenger_mix: list[PassengerSpec],
) -> list[dict[str, Any]]:
    grouped: dict[str, int] = {}
    for passenger in passenger_mix:
        code = passenger.sabre_code
        grouped[code] = grouped.get(code, 0) + passenger.quantity

    return [
        {
            "Quantity": quantity,
            "Code": code,
            "TPA_Extensions": {
                "VoluntaryChanges": {"Match": "Info"}
            },
        }
        for code, quantity in grouped.items()
    ]


def _seats_requested(passenger_mix: list[PassengerSpec]) -> int:
    return sum(
        item.quantity
        for item in passenger_mix
        if item.type in {
            PassengerKind.ADULT,
            PassengerKind.CHILD,
        }
    )


def group_segments_by_legs(
    snapshot: BookingOfferSnapshot,
    legs: list[SearchLeg],
) -> list[list]:
    """Map the frozen segment chain to each canonical Shopping O&D leg."""
    if not legs:
        raise RevalidationRequestError(
            "El Booking no tiene legs para reconstruir la revalidación."
        )

    segments = list(snapshot.segments)
    groups: list[list] = []
    cursor = 0

    for leg_index, leg in enumerate(legs, start=1):
        if cursor >= len(segments):
            raise RevalidationRequestError(
                f"Faltan segmentos para el leg {leg_index}."
            )

        first = segments[cursor]
        if not locations_equivalent(first.departure_airport, leg.origin):
            raise RevalidationRequestError(
                f"El leg {leg_index} comienza en {leg.origin}, pero el "
                f"producto congelado comienza en {first.departure_airport}."
            )

        group = []
        while cursor < len(segments):
            segment = segments[cursor]
            if not segment.booking_class:
                raise RevalidationRequestError(
                    f"El segmento {cursor + 1} no tiene booking class."
                )

            group.append(segment)
            cursor += 1

            if locations_equivalent(
                segment.arrival_airport,
                leg.destination,
            ):
                break

        if not group or not locations_equivalent(
            group[-1].arrival_airport,
            leg.destination,
        ):
            raise RevalidationRequestError(
                f"No se pudo cerrar el leg {leg_index} en {leg.destination}."
            )

        groups.append(group)

    if cursor != len(segments):
        raise RevalidationRequestError(
            "Quedaron segmentos congelados fuera de los legs del Booking."
        )

    return groups


def build_revalidate_request(
    snapshot: BookingOfferSnapshot,
    legs: list[SearchLeg],
    pcc: str,
) -> dict[str, Any]:
    groups = group_segments_by_legs(snapshot, legs)

    origin_destinations: list[dict[str, Any]] = []
    branded = bool(
        str(snapshot.fare.brand_code or "").strip()
        or str(snapshot.fare.brand_name or "").strip()
        or snapshot.fare.branded_components
    )

    for index, group in enumerate(groups, start=1):
        flights = []
        for segment in group:
            operating = (
                segment.operating_carrier
                or segment.marketing_carrier
            )
            flights.append(
                {
                    "Airline": {
                        "Marketing": segment.marketing_carrier,
                        "Operating": operating,
                    },
                    "Number": _flight_number(segment.flight_number),
                    "ClassOfService": segment.booking_class,
                    "OriginLocation": {
                        "LocationCode": segment.departure_airport
                    },
                    "DestinationLocation": {
                        "LocationCode": segment.arrival_airport
                    },
                    "DepartureDateTime": _local_wall_clock(
                        segment.departure_at
                    ),
                    "ArrivalDateTime": _local_wall_clock(
                        segment.arrival_at
                    ),
                    "Type": "A",
                }
            )

        origin_destinations.append(
            {
                "RPH": str(index),
                "DepartureDateTime": _local_wall_clock(
                    group[0].departure_at
                ),
                "OriginLocation": {
                    "LocationCode": group[0].departure_airport
                },
                "DestinationLocation": {
                    "LocationCode": group[-1].arrival_airport
                },
                "TPA_Extensions": {"Flight": flights},
            }
        )

    traveler_summary: dict[str, Any] = {
        "SeatsRequested": [_seats_requested(snapshot.passenger_mix)],
        "AirTravelerAvail": [
            {
                "PassengerTypeQuantity": _sabre_passenger_types(
                    snapshot.passenger_mix
                )
            }
        ],
    }
    if snapshot.fare.currency:
        price_request_information: dict[str, Any] = {
            "CurrencyCode": snapshot.fare.currency
        }
        brand_id = str(snapshot.fare.brand_code or "").strip().upper()
        if brand_id:
            price_request_information["TPA_Extensions"] = {
                "BrandedFareIndicators": {
                    "SingleBrandedFare": True,
                    "MultipleBrandedFares": True,
                    "ReturnBrandAncillaries": True,
                    "UpsellLimit": 3,
                    "BrandFilters": {
                        "Brand": [
                            {
                                "Code": brand_id,
                                "PreferLevel": "Preferred",
                            }
                        ]
                    },
                }
            }
        elif branded:
            # Legacy fallback only: branded metadata exists but there is no
            # exact Sabre Brand ID to lock. Never use brand_name as Code.
            price_request_information["TPA_Extensions"] = {
                "BrandedFareIndicators": {
                    "MultipleBrandedFares": True,
                    "ReturnBrandAncillaries": True,
                }
            }
        traveler_summary["PriceRequestInformation"] = (
            price_request_information
        )

    return {
        "OTA_AirLowFareSearchRQ": {
            "Version": "5",
            "POS": {
                "Source": [
                    {
                        "PseudoCityCode": pcc,
                        "RequestorID": {
                            "Type": "1",
                            "ID": "1",
                            "CompanyName": {
                                "Code": "TN",
                                "content": "TN",
                            },
                        },
                    }
                ]
            },
            "OriginDestinationInformation": origin_destinations,
            "TravelPreferences": {
                "TPA_Extensions": {
                    "VerificationItinCallLogic": {
                        "Value": "M",
                        "AlwaysCheckAvailability": True,
                    }
                },
                "Baggage": {
                    "RequestType": "A",
                    "Description": True,
                },
            },
            "TravelerInfoSummary": traveler_summary,
            "TPA_Extensions": {
                "IntelliSellTransaction": {
                    "RequestType": {"Name": "REVALIDATE"},
                    "ServiceTag": {"Name": "REVALIDATE"},
                }
            },
        }
    }


class SabreRevalidationProvider:
    provider_name = "sabre_revalidate_v5"

    async def revalidate(
        self,
        snapshot: BookingOfferSnapshot,
        legs: list[SearchLeg],
        *,
        environment: str,
    ) -> SabreRevalidationResult:
        env_name = str(environment).lower()
        if env_name not in {"cert", "prod"}:
            raise RevalidationRequestError(
                f"Entorno inválido para revalidación: {environment}."
            )

        settings = get_settings(env_name)
        payload = build_revalidate_request(
            snapshot,
            legs,
            settings.sabre_pcc,
        )

        async with SabreClient(settings) as client:
            response = await client.post(
                settings.sabre_revalidate_path,
                payload,
            )

        diagnostics = extract_bfm_diagnostics(response)
        options = normalize_bfm_response(response)

        return SabreRevalidationResult(
            options=options,
            transaction_id=diagnostics.get("transaction_id"),
            no_availability=bool(
                diagnostics.get("no_availability")
            ),
            messages=list(diagnostics.get("messages") or []),
        )
