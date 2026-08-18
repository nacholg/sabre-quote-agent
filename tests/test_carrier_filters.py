import pytest
from pydantic import ValidationError

from app.models.quote_request import QuoteSearchRequest
from app.sabre.shopping import build_bfm_request


def _search(**kwargs):
    return QuoteSearchRequest(
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
        **kwargs,
    )


def test_multiple_included_carriers_are_preferred():
    body = build_bfm_request(
        _search(preferred_carriers=["AA", "AR", "LA"]), "RY3A"
    )["OTA_AirLowFareSearchRQ"]
    assert body["TravelPreferences"]["VendorPref"] == [
        {"Code": "AA", "PreferLevel": "Only"},
        {"Code": "AR", "PreferLevel": "Only"},
        {"Code": "LA", "PreferLevel": "Only"},
    ]


def test_excluded_carrier_is_unacceptable():
    body = build_bfm_request(
        _search(excluded_carriers=["AR"]), "RY3A"
    )["OTA_AirLowFareSearchRQ"]
    assert body["TravelPreferences"]["VendorPref"] == [
        {"Code": "AR", "PreferLevel": "Unacceptable"}
    ]


def test_include_and_exclude_can_be_combined():
    body = build_bfm_request(
        _search(preferred_carriers=["AA", "LA"], excluded_carriers=["AR"]), "RY3A"
    )["OTA_AirLowFareSearchRQ"]
    assert body["TravelPreferences"]["VendorPref"] == [
        {"Code": "AA", "PreferLevel": "Only"},
        {"Code": "LA", "PreferLevel": "Only"},
        {"Code": "AR", "PreferLevel": "Unacceptable"},
    ]


def test_same_carrier_cannot_be_included_and_excluded():
    with pytest.raises(ValidationError):
        _search(preferred_carriers=["AR"], excluded_carriers=["AR"])
