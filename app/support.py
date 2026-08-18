from __future__ import annotations

import json
import platform
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models.quote_request import QuoteSearchRequest
from app.services.pricing_rules import pricing_modifier, resolve_pricing_currencies

SENSITIVE_KEYS = {
    "authorization",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "sabre_client_secret",
    "sabre_password",
}
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "date",
    "server",
    "x-correlation-id",
    "x-request-id",
    "x-sabre-transaction-id",
    "transaction-id",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                out[key] = "***REDACTED***"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _secret_values(settings: Settings) -> list[str]:
    values: list[str] = []
    for obj in (settings.sabre_client_secret, settings.sabre_password):
        if obj is None:
            continue
        raw = obj.get_secret_value().strip()
        if raw:
            values.append(raw)
    return values


def _assert_no_secrets(text: str, settings: Settings) -> None:
    for secret in _secret_values(settings):
        if secret and secret in text:
            raise RuntimeError("El support bundle contenía una credencial sensible y fue bloqueado.")
    # Bearer/Basic credentials should never be persisted either.
    if re.search(r"(?i)authorization\s*[\":=]+\s*(bearer|basic)\s+[A-Za-z0-9+/._=-]+", text):
        raise RuntimeError("El support bundle contenía un header Authorization y fue bloqueado.")


def create_support_bundle(
    *,
    settings: Settings,
    search: QuoteSearchRequest,
    payload: dict[str, Any],
    exchange: dict[str, Any] | None,
    diagnostics: dict[str, Any] | None,
    error: str | None = None,
    root: Path = Path("logs"),
) -> Path:
    now = datetime.now(timezone.utc)
    debug_id = uuid.uuid4().hex[:10]
    stamp = now.strftime("%Y%m%d_%H%M%SZ")
    bundle_name = f"{stamp}_bfm_{search.origin}_{search.destination}_{debug_id}"
    folder = root / bundle_name
    folder.mkdir(parents=True, exist_ok=False)

    exchange = _redact(exchange or {})
    diagnostics = _redact(diagnostics or {})

    response_json = exchange.get("response_json")
    response_text = exchange.get("response_text")
    response_headers = {
        key: value
        for key, value in (exchange.get("response_headers") or {}).items()
        if key.lower() in SAFE_RESPONSE_HEADERS
    }

    effective_currencies = resolve_pricing_currencies(
        search.origin, search.destination, search.currency
    )

    summary = {
        "debug_id": debug_id,
        "created_at_utc": now.isoformat(),
        "environment": settings.sabre_env,
        "base_url": settings.base_url,
        "endpoint": settings.sabre_shopping_path,
        "oauth_type": settings.sabre_token_type,
        "client_id": settings.sabre_client_id.get_secret_value(),
        "pcc": settings.sabre_pcc,
        "read_only": settings.sabre_read_only,
        "search": {
            "origin": search.origin,
            "destination": search.destination,
            "departure_date": search.departure_date.isoformat(),
            "return_date": search.return_date.isoformat() if search.return_date else None,
            "adults": search.adults,
            "children": search.children,
            "infants": search.infants,
            "cabin": search.cabin.value,
            "max_stops": search.max_stops,
            "profile": search.request_profile.value,
            "requested_currency": search.currency.value,
            "effective_currencies": effective_currencies,
            "pricing_modifiers": [pricing_modifier(value) for value in effective_currencies],
        },
        "http": {
            "method": exchange.get("method"),
            "url": exchange.get("url"),
            "status_code": exchange.get("status_code"),
            "reason": exchange.get("reason"),
            "elapsed_ms": exchange.get("elapsed_ms"),
            "response_headers": response_headers,
        },
        "sabre": {
            "response_version": diagnostics.get("response_version"),
            "transaction_id": diagnostics.get("transaction_id"),
            "itinerary_count": diagnostics.get("itinerary_count"),
            "no_availability": diagnostics.get("no_availability"),
            "messages": diagnostics.get("messages", []),
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "error": error,
        "security": {
            "authorization_header_included": False,
            "access_token_included": False,
            "client_secret_included": False,
            "password_included": False,
        },
    }

    files: dict[str, Any] = {
        "summary.json": summary,
        "request.json": _redact(payload),
        "response_headers.json": response_headers,
    }
    if response_json is not None:
        files["response.json"] = response_json
    elif response_text is not None:
        files["response.txt"] = str(response_text)

    for filename, content in files.items():
        path = folder / filename
        if isinstance(content, str):
            rendered = content
        else:
            rendered = json.dumps(content, indent=2, ensure_ascii=False, default=str)
        _assert_no_secrets(rendered, settings)
        path.write_text(rendered, encoding="utf-8")

    readme = (
        "Sabre BFM Support Bundle\n"
        "========================\n\n"
        "Este paquete fue generado automáticamente para diagnóstico con Sabre.\n"
        "No incluye Authorization, access_token, Client Secret ni password.\n\n"
        f"Debug ID: {debug_id}\n"
        f"Entorno: {settings.sabre_env}\n"
        f"PCC: {settings.sabre_pcc}\n"
        f"Endpoint: {settings.sabre_shopping_path}\n"
        f"Sabre Transaction ID: {diagnostics.get('transaction_id') or 'no informado'}\n"
    )
    _assert_no_secrets(readme, settings)
    (folder / "README.txt").write_text(readme, encoding="utf-8")

    zip_path = root / f"{bundle_name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.iterdir()):
            zf.write(path, arcname=path.name)

    return zip_path
