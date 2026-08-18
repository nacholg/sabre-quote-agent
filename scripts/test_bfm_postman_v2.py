import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx

from app.config import get_settings
from app.sabre.auth import SabreTokenProvider


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    safe = {}
    for k, v in headers.items():
        if k.lower() in {"authorization", "proxy-authorization", "cookie", "set-cookie"}:
            continue
        safe[k] = v
    return safe


def extract_bfm_summary(payload: dict) -> dict:
    gir = payload.get("groupedItineraryResponse", {})
    messages = gir.get("messages", []) or []
    transaction_id = None
    for msg in messages:
        if msg.get("code") == "TRANSACTIONID":
            transaction_id = msg.get("text") or msg.get("value")
            break

    return {
        "response_version": gir.get("version"),
        "itinerary_count": gir.get("statistics", {}).get("itineraryCount"),
        "transaction_id": transaction_id,
        "messages": messages,
    }


def build_postman_v2_request(pcc: str) -> dict:
    # Replicates the official Sabre Postman "BargainFinderMax_GIR_JSON_basic"
    # structure, updated only for future dates and user's PCC.
    return {
        "OTA_AirLowFareSearchRQ": {
            "OriginDestinationInformation": [
                {
                    "DepartureDateTime": "2026-09-19T00:00:00",
                    "OriginLocation": {"LocationCode": "JFK"},
                    "DestinationLocation": {"LocationCode": "LHR"},
                    "RPH": "1",
                },
                {
                    "DepartureDateTime": "2026-09-26T00:00:00",
                    "OriginLocation": {"LocationCode": "LHR"},
                    "DestinationLocation": {"LocationCode": "JFK"},
                    "RPH": "2",
                },
            ],
            "POS": {
                "Source": [
                    {
                        "PseudoCityCode": pcc,
                        "RequestorID": {
                            "CompanyName": {"Code": "TN"},
                            "ID": "1",
                            "Type": "1",
                        },
                    }
                ]
            },
            "TPA_Extensions": {
                "IntelliSellTransaction": {
                    "RequestType": {"Name": "50ITINS"}
                }
            },
            "TravelerInfoSummary": {
                "AirTravelerAvail": [
                    {
                        "PassengerTypeQuantity": [
                            {"Code": "ADT", "Quantity": 1}
                        ]
                    }
                ]
            },
            "Version": "2",
        }
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["prod", "cert"], default="prod")
    parser.add_argument("--support-bundle", action="store_true")
    args = parser.parse_args()

    settings = get_settings(args.env)

    print("Entorno:", settings.sabre_env)
    print("OAuth:", settings.sabre_token_type)
    print("Endpoint base:", settings.base_url)
    print("BFM endpoint:", "/v2/offers/shop")

    payload = build_postman_v2_request(settings.sabre_pcc)

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    request_path = output_dir / "bfm_postman_v2_request.json"
    response_path = output_dir / "bfm_postman_v2_response.json"
    summary_path = output_dir / "bfm_postman_v2_summary.json"

    request_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    async with httpx.AsyncClient(timeout=settings.sabre_timeout_seconds) as http:
        token = await SabreTokenProvider(settings, http).get_token()

        url = f"{settings.base_url.rstrip('/')}/v2/offers/shop"
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        started = datetime.now(timezone.utc)
        response = await http.post(
            url,
            headers=request_headers,
            json=payload,
        )
        elapsed_ms = int(
            (datetime.now(timezone.utc) - started).total_seconds() * 1000
        )

    print("HTTP:", response.status_code)

    try:
        body = response.json()
        response_path.write_text(
            json.dumps(body, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        bfm = extract_bfm_summary(body)
    except ValueError:
        body = None
        response_path.write_text(response.text, encoding="utf-8")
        bfm = {
            "response_version": None,
            "itinerary_count": None,
            "transaction_id": None,
            "messages": [],
        }

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "debug_id": uuid.uuid4().hex[:10],
        "environment": settings.sabre_env,
        "base_url": settings.base_url,
        "endpoint": "/v2/offers/shop",
        "oauth_type": settings.sabre_token_type,
        "pcc": settings.sabre_pcc,
        "http_status": response.status_code,
        "elapsed_ms": elapsed_ms,
        "search": {
            "origin": "JFK",
            "destination": "LHR",
            "departure": "2026-09-19",
            "return": "2026-09-26",
            "adults": 1,
        },
        **bfm,
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("Itinerarios:", summary["itinerary_count"])
    if summary["transaction_id"]:
        print("Transaction ID:", summary["transaction_id"])

    for message in summary["messages"]:
        text = message.get("text") or message.get("value")
        if text:
            print(
                f"{message.get('severity', '')} "
                f"{message.get('code', '')}: {text}"
            )

    print("Request:", request_path)
    print("Response:", response_path)
    print("Summary:", summary_path)

    if args.support_bundle:
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
        zip_path = logs_dir / f"{stamp}_bfm_postman_v2_{summary['debug_id']}.zip"

        headers_path = output_dir / "bfm_postman_v2_response_headers.json"
        headers_path.write_text(
            json.dumps(
                sanitize_headers(dict(response.headers)),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        readme_path = output_dir / "bfm_postman_v2_README.txt"
        readme_path.write_text(
            "Sabre BFM Postman v2 diagnostic bundle\n"
            "Replicates the official Sabre Postman basic BFM request structure.\n"
            "Authorization token and secrets are intentionally excluded.\n"
            f"Environment: {settings.sabre_env}\n"
            f"Endpoint: {settings.base_url.rstrip('/')}/v2/offers/shop\n"
            f"HTTP: {response.status_code}\n"
            f"Transaction ID: {summary['transaction_id']}\n",
            encoding="utf-8",
        )

        with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
            for p in [
                request_path,
                response_path,
                summary_path,
                headers_path,
                readme_path,
            ]:
                zf.write(p, arcname=p.name)

        print("Support bundle:", zip_path)
        print("Bundle sanitizado: no contiene token, password ni Client Secret.")


if __name__ == "__main__":
    asyncio.run(main())
