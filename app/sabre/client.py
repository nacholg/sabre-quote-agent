import asyncio
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.config import Settings
from app.sabre.auth import SabreTokenProvider
from app.sabre.errors import (
    SabreAPIError,
    SabreWriteAmbiguousError,
    SabreWriteNotSentError,
)


class SabreClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.sabre_timeout_seconds),
            headers={"Accept": "application/json"},
        )
        self.tokens = SabreTokenProvider(settings, self.http)
        # Safe diagnostic snapshot for support bundles. It never stores Authorization/token.
        self.last_exchange: dict[str, Any] | None = None

    async def close(self) -> None:
        await self.http.aclose()

    async def __aenter__(self) -> "SabreClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _normalize_path(self, path_or_url: str) -> str:
        path = urlparse(path_or_url).path if path_or_url.startswith(("http://", "https://")) else path_or_url
        if not path.startswith("/"):
            path = f"/{path}"
        return path.rstrip("/") or "/"

    def _enforce_guardrails(self, path: str) -> None:
        if self.settings.sabre_env.upper() != "PROD" or not self.settings.sabre_read_only:
            return
        normalized = self._normalize_path(path)
        allowed = self.settings.allowed_paths
        if not allowed:
            raise SabreAPIError(403, "READ_ONLY_MODE", "La allowlist de PROD está vacía.")
        if normalized not in allowed:
            raise SabreAPIError(
                403,
                "READ_ONLY_MODE",
                f"Endpoint bloqueado en PROD: {normalized}. Permitidos: {sorted(allowed)}",
            )

    def _capture_exchange(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        response: httpx.Response | None,
        started: float,
        error: str | None = None,
    ) -> None:
        snapshot: dict[str, Any] = {
            "method": "POST",
            "url": url,
            "request_headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Sabre-PCC": self.settings.sabre_pcc,
            },
            "request_json": payload,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": error,
        }
        if response is not None:
            snapshot.update(
                {
                    "status_code": response.status_code,
                    "reason": response.reason_phrase,
                    "response_headers": dict(response.headers),
                }
            )
            try:
                snapshot["response_json"] = response.json()
            except ValueError:
                snapshot["response_text"] = response.text[:20000]
        self.last_exchange = snapshot

    def _capture_write_exchange(
        self,
        *,
        url: str,
        response: httpx.Response | None,
        started: float,
        conversation_id: str,
        error: str | None = None,
        sensitive: bool,
    ) -> None:
        snapshot: dict[str, Any] = {
            "method": "POST",
            "url": url,
            "request_headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Sabre-PCC": self.settings.sabre_pcc,
                "Conversation-ID": conversation_id,
            },
            "conversation_id": conversation_id,
            "elapsed_ms": round(
                (time.perf_counter() - started) * 1000,
                2,
            ),
            "error": error,
            "sensitive_payload_omitted": bool(sensitive),
        }
        if response is not None:
            snapshot.update(
                {
                    "status_code": response.status_code,
                    "reason": response.reason_phrase,
                    "response_headers": dict(response.headers),
                }
            )
        self.last_exchange = snapshot

    async def post_once(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        sensitive: bool = False,
    ) -> dict[str, Any]:
        """POST exactly once.

        This primitive exists for non-idempotent writes such as Create Booking.
        It deliberately has no retry loop. Sensitive mode also prevents PII
        from being copied into last_exchange/support diagnostics.
        """
        self._enforce_guardrails(path)
        url = f"{self.settings.base_url}/{path.lstrip('/')}"
        started = time.perf_counter()
        response: httpx.Response | None = None
        conversation_id = str(uuid4())

        try:
            try:
                token = await self.tokens.get_token()
            except Exception as exc:
                self._capture_write_exchange(
                    url=url,
                    response=None,
                    started=started,
                    conversation_id=conversation_id,
                    error=f"pre_send:{type(exc).__name__}",
                    sensitive=sensitive,
                )
                raise SabreWriteNotSentError(
                    "Create Booking no fue enviado: falló la autenticación "
                    "antes del request."
                ) from exc

            try:
                response = await self.http.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "X-Sabre-PCC": self.settings.sabre_pcc,
                        "Conversation-ID": conversation_id,
                    },
                    json=payload,
                )
            except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
                self._capture_write_exchange(
                    url=url,
                    response=None,
                    started=started,
                    conversation_id=conversation_id,
                    error=type(exc).__name__,
                    sensitive=sensitive,
                )
                raise SabreWriteNotSentError(
                    "Create Booking no pudo establecer conexión con Sabre."
                ) from exc
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                self._capture_write_exchange(
                    url=url,
                    response=None,
                    started=started,
                    conversation_id=conversation_id,
                    error=type(exc).__name__,
                    sensitive=sensitive,
                )
                raise SabreWriteAmbiguousError(
                    "El resultado de Create Booking es ambiguo; "
                    "no se debe reintentar automáticamente."
                ) from exc

            self._capture_write_exchange(
                url=url,
                response=response,
                started=started,
                conversation_id=conversation_id,
                sensitive=sensitive,
            )

            if response.is_error:
                raise SabreAPIError(
                    response.status_code,
                    response.reason_phrase,
                    response.text[:5000],
                )

            try:
                return response.json()
            except ValueError as exc:
                raise SabreWriteAmbiguousError(
                    "Sabre respondió éxito HTTP pero el body no pudo "
                    "interpretarse; el resultado debe reconciliarse."
                ) from exc

        except (SabreAPIError, SabreWriteNotSentError, SabreWriteAmbiguousError):
            raise

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._enforce_guardrails(path)
        url = f"{self.settings.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(self.settings.sabre_max_retries + 1):
            started = time.perf_counter()
            response: httpx.Response | None = None
            try:
                token = await self.tokens.get_token()
                response = await self.http.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "X-Sabre-PCC": self.settings.sabre_pcc,
                    },
                    params=params or {},
                )
                snapshot: dict[str, Any] = {
                    "method": "GET",
                    "url": str(response.request.url),
                    "request_headers": {
                        "Accept": "application/json",
                        "X-Sabre-PCC": self.settings.sabre_pcc,
                    },
                    "request_params": params or {},
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    "status_code": response.status_code,
                    "reason": response.reason_phrase,
                    "response_headers": dict(response.headers),
                }
                try:
                    snapshot["response_json"] = response.json()
                except ValueError:
                    snapshot["response_text"] = response.text[:20000]
                self.last_exchange = snapshot

                if response.is_error:
                    raise SabreAPIError(
                        response.status_code,
                        response.reason_phrase,
                        response.text[:5000],
                    )
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError, SabreAPIError) as exc:
                last_error = exc
                retryable = not isinstance(exc, SabreAPIError) or exc.status_code >= 500
                if attempt >= self.settings.sabre_max_retries or not retryable:
                    raise
                await asyncio.sleep(2**attempt)

        raise RuntimeError("Error inesperado al llamar a Sabre") from last_error

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._enforce_guardrails(path)
        url = f"{self.settings.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(self.settings.sabre_max_retries + 1):
            started = time.perf_counter()
            response: httpx.Response | None = None
            try:
                _token_started = time.perf_counter()
                token = await self.tokens.get_token()
                _token_elapsed = time.perf_counter() - _token_started
                print(f"[SABRE] token lookup: {_token_elapsed:.3f}s")
                _http_started = time.perf_counter()
                response = await self.http.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "X-Sabre-PCC": self.settings.sabre_pcc,
                    },
                    json=payload,
                )
                _http_elapsed = time.perf_counter() - _http_started
                print(
                    f"[SABRE] HTTP: {_http_elapsed:.3f}s | "
                    f"attempt={attempt + 1} | HTTP={response.status_code}"
                )
                self._capture_exchange(url=url, payload=payload, response=response, started=started)
                if response.is_error:
                    raise SabreAPIError(
                        response.status_code,
                        response.reason_phrase,
                        response.text[:5000],
                    )
                _json_started = time.perf_counter()
                _payload = response.json()
                _json_elapsed = time.perf_counter() - _json_started
                print(f"[SABRE] JSON parse: {_json_elapsed:.3f}s")
                return _payload
            except (httpx.TimeoutException, httpx.NetworkError, SabreAPIError) as exc:
                last_error = exc
                if self.last_exchange is None or self.last_exchange.get("elapsed_ms") is None:
                    self._capture_exchange(
                        url=url,
                        payload=payload,
                        response=response,
                        started=started,
                        error=str(exc),
                    )
                retryable = not isinstance(exc, SabreAPIError) or exc.status_code >= 500
                if attempt >= self.settings.sabre_max_retries or not retryable:
                    raise
                await asyncio.sleep(2**attempt)

        raise RuntimeError("Error inesperado al llamar a Sabre") from last_error
