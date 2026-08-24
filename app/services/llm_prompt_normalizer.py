from __future__ import annotations

import json
import time
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMInterpreterUnavailable(RuntimeError):
    """The optional LLM normalization layer is unavailable."""


class LLMPromptNormalization(BaseModel):
    canonical_prompt: str = Field(min_length=3, max_length=3000)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LLMInterpreterSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    llm_interpreter_enabled: bool = False
    llm_interpreter_model: str = "gpt-5.6-luna"
    llm_interpreter_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"


@lru_cache(maxsize=2)
def get_llm_interpreter_settings(
    environment: Literal["cert", "prod"],
) -> LLMInterpreterSettings:
    env_file = Path(".env.cert") if environment == "cert" else Path(".env")
    return LLMInterpreterSettings(
        _env_file=env_file if env_file.exists() else None
    )


def llm_fallback_enabled(
    environment: Literal["cert", "prod"],
) -> bool:
    settings = get_llm_interpreter_settings(environment)
    return bool(
        settings.llm_interpreter_enabled
        and settings.openai_api_key is not None
    )


def _json_schema_format() -> dict:
    return {
        "type": "json_schema",
        "name": "flight_quote_prompt_normalization",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "canonical_prompt": {"type": "string"},
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "canonical_prompt",
                "assumptions",
                "warnings",
            ],
            "additionalProperties": False,
        },
    }


def _system_prompt(today: date) -> str:
    return f"""
You normalize natural-language flight shopping requests for a deterministic
Sabre quote parser.

Current date: {today.isoformat()}.

Rewrite the user's request into a compact canonical prompt without changing
their commercial intent.

Canonical conventions:
- Routes: AAA-BBB using 3-letter IATA airport/city codes.
- Round trip: "AAA-BBB <date> regreso <date>".
- Multi-city/open jaw: write every leg explicitly.
- Dates: prefer DDMON or DDMONYYYY.
- If the user omitted a year, do not invent one unless resolving an explicit
  relative phrase such as tomorrow.
- Passengers: 1 ADT, 2 ADT, 1 INF, 2 C09, etc.
- Cabins: ECONOMY, PREMIUM ECONOMY, BUSINESS, FIRST.
- Preserve direct/nonstop, stop limits, airline inclusion/exclusion, currency,
  baggage, branded-fare, and refundable requirements.
- Normalize city/airport names and airline names to IATA codes only when
  unambiguous.

Accuracy rules:
- Never add a destination, date, passenger, airline, cabin, currency, baggage
  condition, refundability condition, or stop condition that was not stated or
  unambiguously implied.
- If required information is missing or ambiguous, leave it missing and add a
  brief warning.
- assumptions must contain only short, user-reviewable assumptions.
""".strip()


def _extract_output_text(body: dict) -> str | None:
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                value = content.get("text")
                if isinstance(value, str) and value.strip():
                    return value
    return None


async def normalize_prompt_with_llm(
    text: str,
    *,
    today: date,
    environment: Literal["cert", "prod"],
) -> LLMPromptNormalization:
    settings = get_llm_interpreter_settings(environment)

    if not settings.llm_interpreter_enabled:
        raise LLMInterpreterUnavailable("LLM interpreter is disabled.")

    if settings.openai_api_key is None:
        raise LLMInterpreterUnavailable(
            "LLM interpreter is enabled but OPENAI_API_KEY is missing."
        )

    payload = {
        "model": settings.llm_interpreter_model,
        "instructions": _system_prompt(today),
        "input": text,
        "text": {
            "format": _json_schema_format(),
        },
    }

    headers = {
        "Authorization": (
            "Bearer " + settings.openai_api_key.get_secret_value()
        ),
        "Content-Type": "application/json",
    }

    url = settings.openai_base_url.rstrip("/") + "/responses"

    started = time.perf_counter()
    print(
        f"[AGENT LLM] start env={environment.upper()} "
        f"model={settings.llm_interpreter_model}"
    )

    try:
        async with httpx.AsyncClient(
            timeout=settings.llm_interpreter_timeout_seconds
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise LLMInterpreterUnavailable(
            "LLM interpreter timed out."
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMInterpreterUnavailable(
            "LLM interpreter transport error."
        ) from exc

    elapsed = time.perf_counter() - started

    if response.status_code >= 400:
        print(
            f"[AGENT LLM] failed status={response.status_code} "
            f"duration={elapsed:.3f}s"
        )
        raise LLMInterpreterUnavailable(
            "LLM interpreter returned HTTP "
            f"{response.status_code}."
        )

    print(
        f"[AGENT LLM] complete status={response.status_code} "
        f"duration={elapsed:.3f}s"
    )

    try:
        body = response.json()
    except ValueError as exc:
        raise LLMInterpreterUnavailable(
            "LLM interpreter returned invalid JSON."
        ) from exc

    content = _extract_output_text(body)
    if content is None:
        raise LLMInterpreterUnavailable(
            "LLM interpreter returned no structured content."
        )

    try:
        parsed = json.loads(content)
        return LLMPromptNormalization.model_validate(parsed)
    except (ValueError, TypeError) as exc:
        raise LLMInterpreterUnavailable(
            "LLM interpreter returned invalid structured content."
        ) from exc
