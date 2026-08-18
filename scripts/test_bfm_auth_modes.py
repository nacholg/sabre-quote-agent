import asyncio
import json
from pathlib import Path

import httpx

from app.config import get_settings
from app.sabre.auth import SabreTokenProvider
from app.sabre.errors import SabreAuthenticationError


PAYLOAD = {
    "OTA_AirLowFareSearchRQ": {
        "Version": "5",
        "POS": {
            "Source": [{
                "PseudoCityCode": "RY3A",
                "RequestorID": {
                    "Type": "1",
                    "ID": "1",
                    "CompanyName": {"Code": "TN"},
                },
            }]
        },
        "OriginDestinationInformation": [{
            "DepartureDateTime": "2026-09-11T20:00:00",
            "OriginLocation": {"LocationCode": "WAW"},
            "DestinationLocation": {"LocationCode": "SPU"},
        }, {
            "DepartureDateTime": "2026-09-18T20:00:00",
            "OriginLocation": {"LocationCode": "SPU"},
            "DestinationLocation": {"LocationCode": "WAW"},
        }],
        "TravelPreferences": {
            "MaxStopsQuantity": 0,
            "VendorPref": [{"Code": "LO"}],
        },
        "TravelerInfoSummary": {
            "AirTravelerAvail": [{
                "PassengerTypeQuantity": [{"Code": "ADT", "Quantity": 1}]
            }]
        },
        "TPA_Extensions": {
            "IntelliSellTransaction": {
                "RequestType": {"Name": "50ITINS"}
            }
        },
    }
}


async def run_mode(mode: str) -> None:
    base = get_settings()
    settings = base.model_copy(update={"sabre_token_type": mode})
    output = Path("output")
    output.mkdir(exist_ok=True)

    async with httpx.AsyncClient(timeout=90) as http:
        try:
            token = await SabreTokenProvider(settings, http).get_token()
        except SabreAuthenticationError as exc:
            print(f"{mode}: TOKEN ERROR: {exc}\n")
            return

        url = f"{settings.base_url}{settings.sabre_shopping_path}"
        response = await http.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Sabre-PCC": settings.sabre_pcc,
            },
            json=PAYLOAD,
        )

    path = output / f"bfm_{mode}_response.json"
    path.write_text(response.text, encoding="utf-8")
    print(f"{mode}: token OK ({len(token)}), BFM HTTP {response.status_code}")
    try:
        body = response.json()
        gir = body.get("groupedItineraryResponse", {})
        print("  itinerarios:", gir.get("statistics", {}).get("itineraryCount"))
        for msg in gir.get("messages", []):
            text = msg.get("text") or msg.get("value")
            if text:
                print(f"  {msg.get('severity')} {msg.get('code')}: {text}")
    except ValueError:
        print("  respuesta:", response.text[:500])
    print("  archivo:", path, "\n")


async def main() -> None:
    for mode in ("password", "client_credentials", "legacy_epr"):
        await run_mode(mode)


if __name__ == "__main__":
    asyncio.run(main())
