from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.models.quote_request import QuoteSearchRequest
from app.sabre.client import SabreClient
from app.sabre.errors import SabreError
from app.sabre.shopping import SabreShoppingService, extract_bfm_diagnostics
from app.services.normalizer import (
    UnsupportedSabreResponse,
    merge_currency_itineraries,
    normalize_bfm_response,
)
from app.services.pricing_rules import resolve_pricing_currencies
from app.services.quote_renderer import render_client_quote

router = APIRouter(prefix="/api/quotes", tags=["quotes"])


@router.post("/search")
async def search_quote(request: QuoteSearchRequest) -> dict:
    settings = get_settings()
    currencies = resolve_pricing_currencies(request.origin, request.destination, request.currency)
    try:
        raw_by_currency: dict[str, dict] = {}
        diagnostics: dict[str, dict] = {}
        normalized_by_currency: dict[str, list] = {}

        async with SabreClient(settings) as client:
            for currency in currencies:
                raw = await SabreShoppingService(client, settings).search(
                    request, currency_override=currency
                )
                raw_by_currency[currency] = raw
                diagnostics[currency] = extract_bfm_diagnostics(raw)
                normalized_by_currency[currency] = normalize_bfm_response(raw)

        keys = list(normalized_by_currency)
        options = normalized_by_currency[keys[0]] if keys else []
        if len(keys) == 2:
            options = merge_currency_itineraries(
                normalized_by_currency[keys[0]], normalized_by_currency[keys[1]]
            )
        options = options[: request.max_options]

        quote_text = (
            render_client_quote(options)
            if options
            else "No se encontraron itinerarios disponibles para los criterios indicados.\n"
        )
        return {
            "status": "ok" if options else "no_availability",
            "effective_currencies": currencies,
            "quote_text": quote_text,
            "options": [option.model_dump(mode="json") for option in options],
            "diagnostics": diagnostics,
        }
    except (SabreError, UnsupportedSabreResponse) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
