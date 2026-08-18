import asyncio
import json

import httpx

from app.config import get_settings
from app.models.quote_request import QuoteSearchRequest
from app.sabre.auth import SabreTokenProvider
from app.sabre.shopping import build_bfm_request


async def main() -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.sabre_timeout_seconds) as http:
        provider = SabreTokenProvider(settings, http)
        token = await provider.get_token()
        print("TOKEN OK", len(token))

        search = QuoteSearchRequest(
            origin="EZE",
            destination="MIA",
            departure_date="2026-09-19",
            adults=1,
            request_profile="official",
        )
        payload = build_bfm_request(search, settings.sabre_pcc)
        url = f"{settings.base_url}/{settings.sabre_shopping_path.lstrip('/')}"
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
        print("BFM HTTP", response.status_code)
        try:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False)[:5000])
        except ValueError:
            print(response.text[:5000])


if __name__ == "__main__":
    asyncio.run(main())
