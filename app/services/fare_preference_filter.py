from __future__ import annotations

from app.models.itinerary import FareOption, ItineraryOption


REFUND_KEYWORDS = ("REFUND BEFORE DEPARTURE", "REFUND AFTER DEPARTURE")


def refund_brand_status(fare: FareOption) -> str | None:
    matches = [
        feature
        for feature in fare.brand_features
        if any(keyword in feature.commercial_name.upper() for keyword in REFUND_KEYWORDS)
    ]
    if not matches:
        return None
    statuses = {feature.application for feature in matches}
    if "F" in statuses:
        return "allowed"
    if "C" in statuses:
        return "with_fee"
    if statuses & {"N", "D"}:
        return "not_allowed"
    return None


def is_confirmed_refundable(fare: FareOption) -> bool:
    """Strict commercial filter.

    Only explicit branded refund attributes count as refundable. A generic
    nonRefundable=false flag is intentionally insufficient (v0.16 policy).
    """
    return refund_brand_status(fare) in {"allowed", "with_fee"}


def _filtered_currency_fares(option: ItineraryOption) -> dict[str, list[FareOption]]:
    result: dict[str, list[FareOption]] = {}
    for currency, fares in (option.fare_options_by_currency or {}).items():
        kept = [fare for fare in fares if is_confirmed_refundable(fare)]
        if kept:
            result[currency] = kept
    return result


def filter_refundable_itineraries(options: list[ItineraryOption]) -> list[ItineraryOption]:
    filtered: list[ItineraryOption] = []

    for option in options:
        by_currency = _filtered_currency_fares(option)

        # Fallback for non-branded normalized structures: still require an
        # explicit branded refund attribute on the fare itself.
        if not by_currency:
            base_fares = option.fares_by_currency or {option.fare.currency: option.fare}
            by_currency = {
                currency: [fare]
                for currency, fare in base_fares.items()
                if is_confirmed_refundable(fare)
            }

        if not by_currency:
            continue

        # Keep representative fare maps aligned with the filtered products.
        fares_by_currency = {
            currency: min(fares, key=lambda f: f.price_per_passenger)
            for currency, fares in by_currency.items()
        }
        preferred = (
            fares_by_currency.get(option.fare.currency)
            or fares_by_currency.get("USD")
            or fares_by_currency.get("ARS")
            or next(iter(fares_by_currency.values()))
        )

        filtered.append(
            option.model_copy(
                update={
                    "fare": preferred,
                    "fares_by_currency": fares_by_currency,
                    "fare_options_by_currency": by_currency,
                }
            )
        )

    return filtered
