from __future__ import annotations

from enum import StrEnum


class PricingCurrency(StrEnum):
    AUTO = "AUTO"
    USD = "USD"
    ARS = "ARS"
    BOTH = "BOTH"


# Airport/city codes used for domestic Argentina pricing detection.
# The list is intentionally kept local and explicit so pricing behavior does not
# depend on an external service. Add codes here as the agency needs them.
ARGENTINA_LOCATION_CODES: set[str] = {
    "AEP", "EZE", "BUE",
    "BHI", "BRC", "CNQ", "COR", "CRD", "CTC", "FMA", "FTE", "IGR", "IRJ",
    "JUJ", "LUQ", "MDQ", "MDZ", "NQN", "PRA", "PSS", "REL", "RES", "RGA",
    "RGL", "ROS", "SDE", "SFN", "SLA", "TUC", "UAQ", "USH", "VDM",
}


def is_domestic_argentina(origin: str, destination: str) -> bool:
    return (
        origin.upper().strip() in ARGENTINA_LOCATION_CODES
        and destination.upper().strip() in ARGENTINA_LOCATION_CODES
    )


def resolve_pricing_currencies(
    origin: str,
    destination: str,
    requested: PricingCurrency | str,
) -> list[str]:
    """Return BFM CurrencyCode values to request, applying agency rules.

    Rules:
    - Domestic Argentina: always ARS, regardless of requested currency.
    - International AUTO: USD.
    - International USD: USD.
    - International ARS: ARS.
    - International BOTH: run one USD and one ARS BFM request.
    """
    requested_value = PricingCurrency(str(requested).upper())
    if is_domestic_argentina(origin, destination):
        return ["ARS"]
    if requested_value == PricingCurrency.ARS:
        return ["ARS"]
    if requested_value == PricingCurrency.BOTH:
        return ["USD", "ARS"]
    return ["USD"]


def pricing_modifier(currency: str) -> str:
    value = currency.upper()
    if value == "ARS":
        return "MARS"
    if value == "USD":
        return "MUSD"
    raise ValueError(f"Moneda de pricing no soportada: {currency}")


def is_domestic_argentina_legs(legs) -> bool:
    return bool(legs) and all(
        is_domestic_argentina(leg.origin, leg.destination)
        for leg in legs
    )


def resolve_pricing_currencies_for_legs(legs, requested: PricingCurrency | str) -> list[str]:
    requested_value = PricingCurrency(str(requested).upper())
    if is_domestic_argentina_legs(legs):
        return ["ARS"]
    if requested_value == PricingCurrency.ARS:
        return ["ARS"]
    if requested_value == PricingCurrency.BOTH:
        return ["USD", "ARS"]
    return ["USD"]
