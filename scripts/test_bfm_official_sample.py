import asyncio
import json
from pathlib import Path

import httpx

from app.config import get_settings
from app.sabre.auth import SabreTokenProvider


OUTPUT = Path("output")
OUTPUT.mkdir(exist_ok=True)


async def main() -> None:
    settings = get_settings()

    payload = {
        "OTA_AirLowFareSearchRQ": {
            "Version": "5",
            "POS": {
                "Source": [
                    {
                        "PseudoCityCode": settings.sabre_pcc,
                        "RequestorID": {
                            "Type": "1",
                            "ID": "1",
                            "CompanyName": {
                                "Code": "TN"
                            }
                        }
                    }
                ]
            },
            "OriginDestinationInformation": [
                {
                    "DepartureDateTime": "2026-09-11T20:00:00",
                    "OriginLocation": {
                        "LocationCode": "WAW"
                    },
                    "DestinationLocation": {
                        "LocationCode": "SPU"
                    }
                },
                {
                    "DepartureDateTime": "2026-09-18T20:00:00",
                    "OriginLocation": {
                        "LocationCode": "SPU"
                    },
                    "DestinationLocation": {
                        "LocationCode": "WAW"
                    }
                }
            ],
            "TravelPreferences": {
                "MaxStopsQuantity": 0,
                "VendorPref": [
                    {
                        "Code": "LO"
                    }
                ]
            },
            "TravelerInfoSummary": {
                "AirTravelerAvail": [
                    {
                        "PassengerTypeQuantity": [
                            {
                                "Code": "ADT",
                                "Quantity": 1
                            }
                        ]
                    }
                ]
            },
            "TPA_Extensions": {
                "IntelliSellTransaction": {
                    "RequestType": {
                        "Name": "50ITINS"
                    }
                }
            }
        }
    }

    request_path = OUTPUT / "bfm_official_sample_request.json"
    request_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    async with httpx.AsyncClient(timeout=90) as http:
        token = await SabreTokenProvider(settings, http).get_token()

        url = (
            f"{settings.base_url.rstrip('/')}/"
            f"{settings.sabre_shopping_path.lstrip('/')}"
        )

        response = await http.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Sabre-PCC": settings.sabre_pcc,
            },
            json=payload,
        )

    response_path = OUTPUT / "bfm_official_sample_response.json"
    response_path.write_text(response.text, encoding="utf-8")

    print("Token OK")
    print("HTTP:", response.status_code)
    print("Request:", request_path)
    print("Response:", response_path)

    try:
        body = response.json()
        gir = body.get("groupedItineraryResponse", {})
        count = gir.get("statistics", {}).get("itineraryCount")
        print("Itinerarios:", count)

        for message in gir.get("messages", []):
            text = message.get("text") or message.get("value")
            if text:
                print(
                    f"{message.get('severity', '')} "
                    f"{message.get('code', '')}: {text}"
                )
    except ValueError:
        print(response.text[:1500])


if __name__ == "__main__":
    asyncio.run(main())