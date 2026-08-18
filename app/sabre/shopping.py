from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.models.quote_request import Cabin, FarePreference, QuoteSearchRequest, RequestProfile
from app.sabre.client import SabreClient
from app.services.pricing_rules import pricing_modifier, resolve_pricing_currencies_for_legs

CABIN_CODES = {
    Cabin.ECONOMY: "Y",
    Cabin.PREMIUM_ECONOMY: "S",
    Cabin.BUSINESS: "C",
    Cabin.FIRST: "F",
}


class _Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    LocationCode: str = Field(min_length=3, max_length=3)


class _OriginDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    DepartureDateTime: str
    OriginLocation: _Location
    DestinationLocation: _Location
    RPH: str | None = None


class _RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")
    Version: str
    POS: dict[str, Any]
    OriginDestinationInformation: list[_OriginDestination] = Field(min_length=1, max_length=10)
    TravelerInfoSummary: dict[str, Any]


class _BFMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    OTA_AirLowFareSearchRQ: _RequestEnvelope


def _date_time(value, local_time) -> str:
    return f"{value.isoformat()}T{local_time.strftime('%H:%M:%S')}"


def _passenger_types(search: QuoteSearchRequest) -> list[dict[str, Any]]:
    return [dict(item) for item in search.sabre_passenger_types()]



def build_bfm_request(
    search: QuoteSearchRequest,
    pcc: str = "XXXX",
    *,
    currency_override: str | None = None,
) -> dict[str, Any]:
    legs = search.effective_legs()
    origin_destinations = [
        {
            "DepartureDateTime": _date_time(leg.departure_date, leg.departure_time),
            "OriginLocation": {"LocationCode": leg.origin},
            "DestinationLocation": {"LocationCode": leg.destination},
            "RPH": str(index),
        }
        for index, leg in enumerate(legs, start=1)
    ]

    travel_preferences: dict[str, Any] = {"MaxStopsQuantity": search.max_stops}
    vendor_preferences: list[dict[str, Any]] = []
    if search.preferred_carriers:
        vendor_preferences.extend(
            {"Code": code, "PreferLevel": "Only"}
            for code in search.preferred_carriers
        )
    if search.excluded_carriers:
        vendor_preferences.extend(
            {"Code": code, "PreferLevel": "Unacceptable"}
            for code in search.excluded_carriers
        )
    if vendor_preferences:
        travel_preferences["VendorPref"] = vendor_preferences

    traveler_summary: dict[str, Any] = {
        "AirTravelerAvail": [{"PassengerTypeQuantity": _passenger_types(search)}]
    }

    request: dict[str, Any] = {
        "Version": "5",
        "POS": {
            "Source": [{
                "PseudoCityCode": pcc,
                "RequestorID": {"Type": "1", "ID": "1", "CompanyName": {"Code": "TN"}},
            }]
        },
        "OriginDestinationInformation": origin_destinations,
        "TravelPreferences": travel_preferences,
        "TravelerInfoSummary": traveler_summary,
        "TPA_Extensions": {"IntelliSellTransaction": {"RequestType": {"Name": "50ITINS"}}},
    }

    if search.request_profile == RequestProfile.STANDARD:
        effective_currency = currency_override or resolve_pricing_currencies_for_legs(
            legs, search.currency
        )[0]
        request["AvailableFlightsOnly"] = True
        request["ResponseType"] = "GIR-JSON"
        travel_preferences["CabinPref"] = [
            {"Cabin": CABIN_CODES[search.cabin], "PreferLevel": "Preferred"}
        ]

        baggage_request: dict[str, Any] = {"RequestType": "C", "Description": True}
        if search.fare_preference == FarePreference.BAGGAGE:
            # Sabre official BFM sample: lowest fare with at least one free checked piece.
            baggage_request["FreePieceRequired"] = True
        if search.request_baggage:
            travel_preferences["Baggage"] = baggage_request

        price_extensions: dict[str, Any] = {}
        if search.fare_preference in {FarePreference.BRANDED, FarePreference.REFUNDABLE, FarePreference.AUTO}:
            # Sabre official Multiple Branded Fares sample.
            price_extensions["BrandedFareIndicators"] = {
                "MultipleBrandedFares": True,
                "ReturnBrandAncillaries": True,
                "UpsellLimit": 3,
            }

        traveler_summary["SeatsRequested"] = [search.seats_requested]
        traveler_summary["PriceRequestInformation"] = {
            "CurrencyCode": effective_currency,
            "TPA_Extensions": price_extensions,
        }
        _ = pricing_modifier(effective_currency)

    payload = {"OTA_AirLowFareSearchRQ": request}
    _BFMRequest.model_validate(payload)
    return payload


def build_bfm_requests(search: QuoteSearchRequest, pcc: str) -> dict[str, dict[str, Any]]:
    currencies = resolve_pricing_currencies_for_legs(search.effective_legs(), search.currency)
    if search.request_profile == RequestProfile.OFFICIAL:
        return {"OFFICIAL": build_bfm_request(search, pcc)}
    return {currency: build_bfm_request(search, pcc, currency_override=currency) for currency in currencies}


def extract_bfm_diagnostics(response: dict[str, Any]) -> dict[str, Any]:
    root = response.get("groupedItineraryResponse") or {}
    messages = root.get("messages") or []
    return {
        "response_version": root.get("version"),
        "itinerary_count": (root.get("statistics") or {}).get("itineraryCount", 0),
        "transaction_id": next(
            (item.get("text") for item in messages if item.get("code") == "TRANSACTIONID"), None
        ),
        "messages": messages,
        "no_availability": any(
            item.get("text") == "No Availability" or item.get("value") == "No Availability"
            for item in messages
        ),
    }


class SabreShoppingService:
    def __init__(self, client: SabreClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def search(self, search: QuoteSearchRequest, *, currency_override: str | None = None) -> dict[str, Any]:
        payload = build_bfm_request(search, self.settings.sabre_pcc, currency_override=currency_override)
        return await self.client.post(self.settings.sabre_shopping_path, payload)
