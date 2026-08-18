import asyncio
from dataclasses import replace

import httpx

from app.config import get_settings
from app.sabre.auth import SabreTokenProvider
from app.sabre.errors import SabreAuthenticationError


async def test_mode(mode: str) -> tuple[str, str]:
    base = get_settings()
    settings = base.model_copy(update={"sabre_token_type": mode})

    async with httpx.AsyncClient(timeout=settings.sabre_timeout_seconds) as http:
        try:
            token = await SabreTokenProvider(settings, http).get_token()
            return mode, f"OK - longitud {len(token)}"
        except SabreAuthenticationError as exc:
            return mode, f"ERROR - {exc}"


async def main() -> None:
    print("Probando los tres flujos OAuth sin mostrar secretos:\n")
    for mode in ("password", "client_credentials", "legacy_epr"):
        name, result = await test_mode(mode)
        print(f"{name}: {result}\n")


if __name__ == "__main__":
    asyncio.run(main())
