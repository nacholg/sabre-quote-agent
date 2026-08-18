import argparse
import asyncio

import httpx

from app.config import get_settings
from app.sabre.auth import SabreTokenProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba autenticación Sabre")
    parser.add_argument("--env", choices=["prod", "cert"], default="prod")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings(args.env)

    print(f"Entorno: {settings.sabre_env}")
    print(f"OAuth: {settings.sabre_token_type}")
    print(f"Endpoint base: {settings.base_url}")
    if settings.sabre_token_type == "password":
        print(f"Usuario: {settings.resolved_username}")

    async with httpx.AsyncClient(timeout=settings.sabre_timeout_seconds) as http:
        token = await SabreTokenProvider(settings, http).get_token()

    print("TOKEN OK")
    print("Longitud:", len(token))


if __name__ == "__main__":
    asyncio.run(main())
