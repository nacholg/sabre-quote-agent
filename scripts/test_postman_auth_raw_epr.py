import base64
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("SABRE_BASE_URL", "https://api.platform.sabre.com").rstrip("/")
FULL_USERNAME = (os.getenv("SABRE_USERNAME") or os.getenv("SABRE_EPR") or "").strip()
PASSWORD = (os.getenv("SABRE_PASSWORD") or "").strip()

# Para OAuth v2 legacy, el Postman oficial mantiene username y PCC por separado.
# Si SABRE_EPR_RAW está definido, se usa. Si no, toma lo anterior al primer guion.
RAW_EPR = (os.getenv("SABRE_EPR_RAW") or "").strip()
if not RAW_EPR and FULL_USERNAME:
    RAW_EPR = FULL_USERNAME.split("-", 1)[0]


def b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def build_postman_credential(username: str, password: str) -> str:
    b64_user = b64(username)
    b64_pass = b64(password)
    return b64(f"{b64_user}:{b64_pass}")


def main() -> None:
    if not RAW_EPR or not PASSWORD:
        raise SystemExit("Faltan EPR o SABRE_PASSWORD en el .env")

    url = f"{BASE_URL}/v2/auth/token"
    encoded = build_postman_credential(RAW_EPR, PASSWORD)

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "Accept": "*/*",
        "grant_type": "client_credentials",
    }

    print("Endpoint:", url)
    print("EPR usado:", RAW_EPR)
    print("Modo:", "OAuth v2 legacy EPR puro")
    print("Authorization length:", len(headers["Authorization"]))

    response = httpx.post(url, headers=headers, data=data, timeout=30)

    print("HTTP:", response.status_code)
    print("Content-Type:", response.headers.get("content-type"))

    try:
        payload = response.json()
    except ValueError:
        print(response.text[:2000])
        return

    token = payload.pop("access_token", None)
    print("Response:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if token:
        print("TOKEN OK")
        print("Longitud:", len(token))
    else:
        print("No se recibió access_token.")


if __name__ == "__main__":
    main()
