from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # SOAP / Sabre Web Services.
    # Can be overridden in .env/.env.cert with SABRE_SOAP_ENDPOINT.
    sabre_soap_endpoint: str | None = None
    sabre_cert_soap_endpoint: str = "https://webservices.cert.platform.sabre.com/websvc"
    sabre_prod_soap_endpoint: str = "https://webservices.platform.sabre.com/websvc"

    sabre_timeout_seconds: float = Field(default=60, gt=0)
    sabre_max_retries: int = Field(default=2, ge=0, le=5)

    # PROD guard rail. Shopping is read-only, but still uses POST.
    sabre_read_only: bool = True
    sabre_allowed_paths_prod: str = "/v5/offers/shop"

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
    env_file = Path(".env.cert") if env_name.lower() == "cert" else Path(".env")
    if not env_file.exists():
        raise FileNotFoundError(f"No se encontró el archivo {env_file.resolve()}")
    return Settings(_env_file=env_file)  # type: ignore[call-arg]
