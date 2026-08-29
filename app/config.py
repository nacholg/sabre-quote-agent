from functools import lru_cache
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SabreEnvironmentMismatchError(RuntimeError):
    """Requested Sabre environment does not match explicit runtime config."""


def _requested_sabre_env(env_name: Literal["prod", "cert"]) -> str:
    return "CERT" if env_name.lower() == "cert" else "PROD"


def _validate_environment_match(
    settings: "Settings",
    env_name: Literal["prod", "cert"],
    *,
    explicit_source: bool,
) -> None:
    if not explicit_source:
        return

    expected = _requested_sabre_env(env_name)
    actual = (settings.sabre_env or "").strip().upper()

    if actual not in {"CERT", "PROD"}:
        raise SabreEnvironmentMismatchError(
            "SABRE_ENV debe ser CERT o PROD; "
            f"valor configurado: {actual or '<vacío>'}."
        )

    if actual != expected:
        raise SabreEnvironmentMismatchError(
            f"Entorno solicitado {expected}, pero este runtime "
            f"está configurado para {actual}. "
            "No se realizará ninguna llamada a Sabre."
        )


def runtime_environment_status() -> dict[str, object]:
    """Return non-secret runtime environment metadata for UI/operations."""
    cert_file = Path(".env.cert").exists()
    prod_file = Path(".env").exists()
    process_env = (os.getenv("SABRE_ENV") or "").strip().upper()

    if not cert_file and not prod_file and process_env in {"CERT", "PROD"}:
        environment = "cert" if process_env == "CERT" else "prod"
        return {
            "locked": True,
            "environment": environment,
            "available_environments": [environment],
            "source": "process",
            "read_only": True,
        }

    available: list[str] = []
    if cert_file:
        available.append("cert")
    if prod_file:
        available.append("prod")

    return {
        "locked": False,
        "environment": None,
        "available_environments": available or ["cert", "prod"],
        "source": "dotenv" if available else "defaults",
        "read_only": True,
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Environment
    sabre_env: str = "PROD"
    sabre_environment: Literal["cert", "production"] = "production"
    sabre_base_url: str | None = None

    # OAuth
    sabre_token_type: Literal["client_credentials", "password", "legacy_epr"] = "password"
    sabre_client_id: SecretStr
    sabre_client_secret: SecretStr
    sabre_username: str | None = None
    sabre_password: SecretStr | None = None

    # Optional legacy fields, kept for compatibility.
    sabre_epr: str | None = None
    sabre_pcc: str
    sabre_aaa: str = "AA"

    sabre_cert_base_url: str = "https://api.cert.platform.sabre.com"
    sabre_prod_base_url: str = "https://api.platform.sabre.com"
    sabre_v2_token_path: str = "/v2/auth/token"
    sabre_v3_token_path: str = "/v3/auth/token"
    sabre_shopping_path: str = "/v5/offers/shop"
    sabre_revalidate_path: str = "/v5/shop/flights/revalidate"
    sabre_create_booking_path: str = "/v1/trip/orders/createBooking"

    # Create Booking is a write and must be explicitly enabled.
    # PROD additionally requires its own second opt-in.
    sabre_create_booking_enabled: bool = False
    sabre_create_booking_prod_enabled: bool = False

    # Experimental stateful SOAP PQ retain/write gate. CERT harness only.
    sabre_pnr_pricing_enabled: bool = False

    # Experimental existing-PNR Secure Flight write. CERT harness only.
    sabre_secure_flight_enabled: bool = False

    # SOAP / Sabre Web Services.
    # Can be overridden in .env/.env.cert with SABRE_SOAP_ENDPOINT.
    sabre_soap_endpoint: str | None = None
    sabre_cert_soap_endpoint: str = "https://webservices.cert.platform.sabre.com/websvc"
    sabre_prod_soap_endpoint: str = "https://webservices.platform.sabre.com/websvc"

    sabre_timeout_seconds: float = Field(default=60, gt=0)
    sabre_max_retries: int = Field(default=2, ge=0, le=5)

    # PROD guard rail. Shopping/Revalidate are read-only, but both use POST.
    sabre_read_only: bool = True
    sabre_allowed_paths_prod: str = (
        "/v5/offers/shop,/v5/shop/flights/revalidate"
    )

    @property
    def base_url(self) -> str:
        if self.sabre_base_url:
            return self.sabre_base_url.rstrip("/")
        if self.sabre_env.upper() == "CERT":
            return self.sabre_cert_base_url.rstrip("/")
        return self.sabre_prod_base_url.rstrip("/")

    @property
    def soap_endpoint(self) -> str:
        if self.sabre_soap_endpoint and self.sabre_soap_endpoint.strip():
            return self.sabre_soap_endpoint.strip().rstrip("/")
        if self.sabre_env.upper() == "CERT":
            return self.sabre_cert_soap_endpoint.rstrip("/")
        return self.sabre_prod_soap_endpoint.rstrip("/")

    @property
    def resolved_username(self) -> str:
        if self.sabre_username and self.sabre_username.strip():
            return self.sabre_username.strip()
        if self.sabre_epr:
            value = self.sabre_epr.strip()
            suffix = f"-{self.sabre_pcc.upper()}-{self.sabre_aaa.upper()}"
            if value.upper().endswith(suffix):
                return value
            return f"{value}-{self.sabre_pcc}-{self.sabre_aaa}"
        return ""

    @property
    def allowed_paths(self) -> set[str]:
        paths: set[str] = set()
        for item in self.sabre_allowed_paths_prod.split(","):
            path = item.strip()
            if not path:
                continue
            if not path.startswith("/"):
                path = f"/{path}"
            paths.add(path.rstrip("/") or "/")
        return paths


@lru_cache
def get_settings(env_name: Literal["prod", "cert"] = "prod") -> Settings:
    """Load Sabre settings from a local dotenv file when present.

    Local development keeps the historical behavior:
      - cert -> .env.cert
      - prod -> .env

    In deployed environments such as Railway those files are intentionally
    absent, so Settings falls back to the process environment variables.
    """
    env_file = Path(".env.cert") if env_name.lower() == "cert" else Path(".env")

    if env_file.exists():
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
        _validate_environment_match(
            settings,
            env_name,
            explicit_source=True,
        )
        return settings

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    _validate_environment_match(
        settings,
        env_name,
        explicit_source=bool(os.getenv("SABRE_ENV")),
    )
    return settings
