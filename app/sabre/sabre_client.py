# sabre_client.py
#
# Supports BOTH Sabre OAuth modes commonly used for Sabre APIs:
#
# A) client_credentials (sessionless) -> POST /v2/auth/token
#    Authorization: Basic base64(client_id:client_secret)
#    Body: grant_type=client_credentials
#
# B) password grant (user context) -> POST /v3/auth/token
#    Authorization: Basic base64(client_id:client_secret)
#    Body: grant_type=password&username=...&password=...
#
# Select with:
#   SABRE_TOKEN_TYPE=client_credentials  (default)
#   SABRE_TOKEN_TYPE=password
#
# For password grant, set:
#   SABRE_USERNAME=...
#   SABRE_PASSWORD=...
#
# Guard rails (recommended):
#   SABRE_ENV=PROD
#   SABRE_READ_ONLY=1
#   SABRE_ALLOWED_PATHS_PROD=/v1/trip/orders/getBooking,/v1/trip/orders/getBookingHistory
#
# Loads .env automatically if python-dotenv is installed.

import os
import time
import base64
from dataclasses import dataclass
from typing import Optional, Dict, Any, Set
from urllib.parse import urlparse

import requests

# -------------------------------------------------
# Load .env automatically (if present)
# -------------------------------------------------
try:
    from dotenv import load_dotenv
    from pathlib import Path

    # 1) current working directory
    load_dotenv()

    # 2) same directory as this file
    here = Path(__file__).resolve().parent
    env_path = here / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
except Exception:
    pass


# -------------------------------------------------
# Exceptions
# -------------------------------------------------
class SabreAuthError(RuntimeError):
    pass


class SabreConfigError(RuntimeError):
    pass


class SabreWriteBlocked(RuntimeError):
    """Raised when an endpoint call is blocked by read-only guard rails."""
    pass


# -------------------------------------------------
# Config
# -------------------------------------------------
@dataclass
class SabreConfig:
    base_url: str
    client_id: str
    client_secret: str
    pcc: Optional[str] = None
    env: Optional[str] = None
    timeout: int = 30

    # Guard rails
    read_only: bool = False
    allowed_paths_prod: Optional[Set[str]] = None

    @staticmethod
    def _parse_bool(val: str, default: bool = False) -> bool:
        if val is None:
            return default
        v = str(val).strip().lower()
        return v not in ("0", "false", "no", "off", "")

    @staticmethod
    def _parse_allowed_paths(raw: str) -> Set[str]:
        """
        Comma-separated list of paths.
        Normalizes:
          - ensures leading '/'
          - strips spaces
          - strips trailing '/'
        """
        out: Set[str] = set()
        for item in (raw or "").split(","):
            p = item.strip()
            if not p:
                continue
            if not p.startswith("/"):
                p = "/" + p
            p = p.rstrip("/") or "/"
            out.add(p)
        return out

    @staticmethod
    def from_env() -> "SabreConfig":
        required = ["SABRE_BASE_URL", "SABRE_CLIENT_ID", "SABRE_CLIENT_SECRET"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise SabreConfigError(
                "Missing environment variables: "
                + ", ".join(missing)
                + ". Ensure your .env is loaded or set vars in PowerShell."
            )

        env = (os.getenv("SABRE_ENV", "") or "").strip() or None

        # Guard rails
        read_only = SabreConfig._parse_bool(os.getenv("SABRE_READ_ONLY", "0"), default=False)

        # Allowlist: if not provided, we keep a safe default (only token endpoints are irrelevant here)
        raw_allow = os.getenv("SABRE_ALLOWED_PATHS_PROD", "").strip()
        allowed_paths_prod = SabreConfig._parse_allowed_paths(raw_allow) if raw_allow else set()

        return SabreConfig(
            base_url=os.environ["SABRE_BASE_URL"].strip().rstrip("/"),
            client_id=os.environ["SABRE_CLIENT_ID"].strip(),
            client_secret=os.environ["SABRE_CLIENT_SECRET"].strip(),
            pcc=os.getenv("SABRE_PCC", "").strip() or None,
            env=env,
            timeout=int(os.getenv("SABRE_TIMEOUT", "30")),
            read_only=read_only,
            allowed_paths_prod=allowed_paths_prod,
        )


# -------------------------------------------------
# Client
# -------------------------------------------------
class SabreClient:
    def __init__(self, cfg: SabreConfig):
        self.cfg = cfg
        self._token: Optional[str] = None
        self._token_exp_epoch: float = 0.0

    # -------- Auth helpers --------
    def _basic_standard(self) -> str:
        """
        Standard Basic auth: base64(client_id:client_secret)
        """
        raw = f"{self.cfg.client_id}:{self.cfg.client_secret}".encode("utf-8")
        b64 = base64.b64encode(raw).decode("ascii")
        return f"Basic {b64}"

    # -------- Auth main --------
    def get_access_token(self, force_refresh: bool = False) -> str:
        """
        Gets an OAuth token using either:
          - client_credentials (default): /v2/auth/token
          - password: /v3/auth/token (requires SABRE_USERNAME and SABRE_PASSWORD)

        Select with env:
          SABRE_TOKEN_TYPE=client_credentials | password
        """
        now = time.time()
        if (not force_refresh) and self._token and now < (self._token_exp_epoch - 30):
            return self._token

        token_type = os.getenv("SABRE_TOKEN_TYPE", "client_credentials").strip().lower()

        if token_type == "password":
            token_url = f"{self.cfg.base_url}/v3/auth/token"
            username = os.getenv("SABRE_USERNAME", "").strip()
            password = os.getenv("SABRE_PASSWORD", "").strip()
            if not username or not password:
                raise SabreAuthError(
                    "SABRE_TOKEN_TYPE=password requires SABRE_USERNAME and SABRE_PASSWORD."
                )
            data = {
                "grant_type": "password",
                "username": username,
                "password": password,
            }
        else:
            token_url = f"{self.cfg.base_url}/v2/auth/token"
            data = {"grant_type": "client_credentials"}

        headers = {
            "Authorization": self._basic_standard(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        r = requests.post(token_url, headers=headers, data=data, timeout=self.cfg.timeout)

        if r.status_code != 200:
            raise SabreAuthError(
                f"Sabre token error {r.status_code} from {token_url}. "
                f"Response: {r.text[:1200]}"
            )

        payload = r.json()
        token = payload.get("access_token")
        expires_in = float(payload.get("expires_in", 0) or 0)

        if not token:
            raise SabreAuthError(f"Token response missing access_token. Response: {payload}")

        self._token = token
        self._token_exp_epoch = time.time() + expires_in
        return token

    # -------- HTTP helpers --------
    def _headers(self) -> Dict[str, str]:
        token = self.get_access_token()
        h = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if self.cfg.pcc:
            h["X-Sabre-PCC"] = self.cfg.pcc
        return h

    def _is_prod_read_only(self) -> bool:
        return (self.cfg.env or "").upper() == "PROD" and bool(self.cfg.read_only)

    def _normalize_path(self, path_or_url: str) -> str:
        """
        Returns normalized URL path:
          - If full URL, extracts parsed.path
          - Ensures leading '/'
          - Strips trailing '/'
        """
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            p = urlparse(path_or_url).path
        else:
            p = path_or_url

        p = (p or "").strip()
        if not p.startswith("/"):
            p = "/" + p
        p = p.rstrip("/") or "/"
        return p

    def _enforce_guardrails(self, method: str, path_or_url: str) -> None:
        """
        PROD read-only guard rails:
          - allow only endpoints in cfg.allowed_paths_prod (PATH allowlist)
          - because many read-only Sabre endpoints still use POST
        """
        if not self._is_prod_read_only():
            return

        allowed = self.cfg.allowed_paths_prod or set()
        if not allowed:
            # If read-only is enabled but allowlist is empty -> safest behavior is block everything.
            raise SabreWriteBlocked(
                "READ_ONLY_MODE: SABRE_ALLOWED_PATHS_PROD está vacío. "
                "Por seguridad en PROD, el cliente bloquea todas las llamadas Sabre."
            )

        p = self._normalize_path(path_or_url)

        if p not in allowed:
            raise SabreWriteBlocked(
                f"READ_ONLY_MODE: bloqueado en PROD. Endpoint no permitido: {p}. "
                f"Permitidos: {sorted(list(allowed))}"
            )

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        """
        path can be full URL or relative path starting with /.
        """
        # Guard rails BEFORE building URL
        self._enforce_guardrails(method, path)

        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.cfg.base_url}/{path.lstrip('/')}"

        headers = kwargs.pop("headers", {})
        merged = {**self._headers(), **headers}

        return requests.request(
            method=method.upper(),
            url=url,
            headers=merged,
            timeout=kwargs.pop("timeout", self.cfg.timeout),
            **kwargs,
        )

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)


# -------------------------------------------------
# Loader (backwards compatible)
# -------------------------------------------------
def load_sabre_from_env() -> SabreClient:
    cfg = SabreConfig.from_env()
    return SabreClient(cfg)