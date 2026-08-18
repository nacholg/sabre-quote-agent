import base64
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("SABRE_BASE_URL", "https://api.platform.sabre.com").rstrip("/")
USERNAME = (os.getenv("SABRE_USERNAME") or os.getenv("SABRE_EPR") or "").strip()
PASSWORD = (os.getenv("SABRE_PASSWORD") or "").strip()


def b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def build_postman_credential(username: str, password: str) -> str:
    base64_username = b64(username)
    base64_password = b64(password)
    concatenated = f"{base64_username}:{base64_password}"
    return b64(concatenated)


def main() -> None:
    if not USERNAME or not PASSWORD:
        raise SystemExit(
            "Faltan SABRE_USERNAME/SABRE_EPR o SABRE_PASSWORD en el .env"
        )

    token_url = f"{BASE_URL}/v2/auth/token"
    rest_credentials = build_postman_credential(USERNAME, PASSWORD)

    headers = {
        "Authorization": f"Basic {rest_credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "Accept": "*/*",
        "grant_type": "client_credentials",
    }

    print("Endpoint:", token_url)
    print("Usuario:", USERNAME)
    print("Modo:", "Postman oficial BFM / OAuth v2 legacy EPR")
    print("Authorization length:", len(headers["Authorization"]))

    response = httpx.post(
        token_url,
        headers=headers,
        data=data,
        timeout=30,
    )

    print("HTTP:", response.status_code)
    print("Content-Type:", response.headers.get("content-type"))

    try:
        payload = response.json()
        safe = dict(payload)
        token = safe.pop("access_token", None)

        print("Response:")
        print(json.dumps(safe, indent=2, ensure_ascii=False))

        if token:
            print("TOKEN OK")
            print("Longitud:", len(token))
        else:
            print("No se recibió access_token.")
    except ValueError:
        print("Response body:")
        print(response.text[:2000])


if __name__ == "__main__":
    main()
