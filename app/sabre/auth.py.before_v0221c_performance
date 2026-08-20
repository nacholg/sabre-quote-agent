from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.config import Settings
from app.sabre.errors import SabreAuthenticationError


@dataclass(slots=True)
class AccessToken:
    value: str
    expires_at: datetime

    def is_valid(self, margin_seconds: int = 60) -> bool:
        return datetime.now(timezone.utc) + timedelta(seconds=margin_seconds) < self.expires_at


class SabreTokenProvider:
    """Sabre OAuth provider supporting all relevant credential flows.

    Modes:
      password: OAuth v3 password grant using app client credentials + EPR context.
      client_credentials: OAuth v2 with standard Basic(client_id:client_secret).
      legacy_epr: OAuth v2 legacy double-Base64 using EPR username/password.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    async def get_token(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._token and self._token.is_valid():
            return self._token.value

        async with self._lock:
            if not force_refresh and self._token and self._token.is_valid():
                return self._token.value
            self._token = await self._create_token()
            return self._token.value

    def clear(self) -> None:
        self._token = None

    @staticmethod
    def _b64(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    def _standard_client_basic(self) -> str:
        client_id = self.settings.sabre_client_id.get_secret_value().strip()
        client_secret = self.settings.sabre_client_secret.get_secret_value().strip()
        if not client_id or not client_secret:
            raise SabreAuthenticationError(
                "Faltan SABRE_CLIENT_ID o SABRE_CLIENT_SECRET en el archivo .env."
            )
        return f"Basic {self._b64(f'{client_id}:{client_secret}')}"

    def _legacy_epr_basic(self) -> str:
        username = self.settings.resolved_username
        password_obj = self.settings.sabre_password
        password = password_obj.get_secret_value().strip() if password_obj else ""
        if not username or not password:
            raise SabreAuthenticationError(
                "SABRE_TOKEN_TYPE=legacy_epr requiere SABRE_USERNAME y SABRE_PASSWORD."
            )

        # Sabre OAuth v2 legacy scheme:
        # Base64(Base64(username) + ':' + Base64(password))
        encoded_username = self._b64(username)
        encoded_password = self._b64(password)
        outer = self._b64(f"{encoded_username}:{encoded_password}")
        return f"Basic {outer}"

    async def _create_token(self) -> AccessToken:
        token_type = self.settings.sabre_token_type

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        if token_type == "password":
            username = self.settings.resolved_username
            password_obj = self.settings.sabre_password
            password = password_obj.get_secret_value().strip() if password_obj else ""
            if not username or not password:
                raise SabreAuthenticationError(
                    "SABRE_TOKEN_TYPE=password requiere SABRE_USERNAME y SABRE_PASSWORD."
                )
            url = f"{self.settings.base_url}{self.settings.sabre_v3_token_path}"
            headers["Authorization"] = self._standard_client_basic()
            data = {
                "grant_type": "password",
                "username": username,
                "password": password,
            }
        elif token_type == "legacy_epr":
            url = f"{self.settings.base_url}{self.settings.sabre_v2_token_path}"
            headers["Authorization"] = self._legacy_epr_basic()
            data = {"grant_type": "client_credentials"}
        else:
            url = f"{self.settings.base_url}{self.settings.sabre_v2_token_path}"
            headers["Authorization"] = self._standard_client_basic()
            data = {"grant_type": "client_credentials"}

        try:
            response = await self.client.post(url, headers=headers, data=data)
        except httpx.HTTPError as exc:
            raise SabreAuthenticationError(f"No se pudo conectar al endpoint OAuth: {exc}") from exc

        if response.is_error:
            raise SabreAuthenticationError(
                f"OAuth Sabre ({token_type}) devolvió HTTP {response.status_code} desde {url}: "
                f"{response.text[:1200]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SabreAuthenticationError("La respuesta OAuth no es JSON válido.") from exc

        token = payload.get("access_token")
        expires_in = int(float(payload.get("expires_in", 300) or 300))
        if not token:
            raise SabreAuthenticationError(
                f"La respuesta OAuth no contiene access_token: {payload}"
            )

        return AccessToken(
            value=str(token),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )
