from __future__ import annotations

import json
import time
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.models.api import QuoteSearchAPIRequest
from app.services.llm_prompt_normalizer import (
    LLMInterpreterUnavailable,
    get_llm_interpreter_settings,
)


class LLMQuoteModificationNormalization(BaseModel):
    canonical_instruction: str = Field(min_length=1, max_length=1000)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification: str | None = Field(default=None, max_length=500)


def _json_schema_format() -> dict:
    return {
        "type": "json_schema",
        "name": "flight_quote_modification_normalization",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "canonical_instruction": {"type": "string"},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "needs_clarification": {"type": "boolean"},
                "clarification": {"type": ["string", "null"]},
            },
            "required": [
                "canonical_instruction",
                "assumptions",
                "warnings",
                "needs_clarification",
                "clarification",
            ],
            "additionalProperties": False,
        },
    }


def _base_context(base: QuoteSearchAPIRequest) -> dict:
    legs = [
        {
            "origin": leg.origin,
            "destination": leg.destination,
            "departure_date": leg.departure_date.isoformat(),
        }
        for leg in base.legs
    ]
    if not legs:
        legs = [
            {
                "origin": base.origin,
                "destination": base.destination,
                "departure_date": base.departure_date.isoformat() if base.departure_date else None,
            }
        ]
        if base.return_date:
            legs.append(
                {
                    "origin": base.destination,
                    "destination": base.origin,
                    "departure_date": base.return_date.isoformat(),
                }
            )
    return {
        "legs": legs,
        "adults": base.adults,
        "children": base.children,
        "infants": base.infants,
        "cabin": base.cabin.value,
        "direct": base.direct,
        "max_stops": base.max_stops,
        "carriers": list(base.carriers),
        "excluded_carriers": list(base.excluded_carriers),
        "fare_preference": base.fare_preference.value,
    }


def _system_prompt(today: date, base: QuoteSearchAPIRequest) -> str:
    context = json.dumps(_base_context(base), ensure_ascii=False, separators=(",", ":"))
    return f"""
You normalize a FOLLOW-UP instruction that modifies an existing flight quote.
You do not create a new flight request and you do not talk to Sabre.

Current date: {today.isoformat()}.
Current quote context: {context}

Return one compact canonical modification instruction that the deterministic
quote-change parser can understand.

Supported canonical instructions:
- passengers: "cotizar para 2 personas"
- cabin: "probalo en ECONOMY", "probalo en BUSINESS"
- departure date: "salir el 31OCT2026"
- return date: "volver el 03NOV2026"
- direct: "solo directos"
- allow one stop: "permitir una escala"
- included carrier: "solo AM"
- excluded carrier: "excluir AV"
- baggage: "con equipaje"
- refundable: "refundable"
- branded fares: "branded"
- lowest fare: "lowest"
- automatic fare preference: "tarifa auto"

You may combine multiple supported changes in one canonical instruction.
Resolve relative wording against the CURRENT quote context when the requested
change is unambiguous. Example: if the return is 2026-11-01 and the user says
"corramos la vuelta un par de días", canonicalize to "volver el 03NOV2026".

Safety rules:
- Never invent a change that the user did not request.
- Never change origin, destination, route shape, passenger ages, or add/remove
  flight legs. Those operations are not supported here.
- Passenger changes only support replacing the explicit ADULT total. Never add,
  remove, infer, or change children, infants, passenger ages, or relative
  passenger counts such as "add one passenger". Ask for clarification instead.
- Never infer whether a lone date refers to departure or return on a round trip.
- If the request is vague, unsupported, missing a factual value, or ambiguous,
  set needs_clarification=true and ask a short question in clarification.
- When needs_clarification=true, canonical_instruction must be "NO_CHANGE".
- assumptions must only contain short user-reviewable interpretations.
- warnings are only for non-blocking uncertainty.
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


async def normalize_quote_modification_with_llm(
    text: str,
    *,
    base: QuoteSearchAPIRequest,
    today: date,
    environment: Literal["cert", "prod"],
) -> LLMQuoteModificationNormalization:
    settings = get_llm_interpreter_settings(environment)
    if not settings.llm_interpreter_enabled:
        raise LLMInterpreterUnavailable("LLM interpreter is disabled.")
    if settings.openai_api_key is None:
        raise LLMInterpreterUnavailable(
            "LLM interpreter is enabled but OPENAI_API_KEY is missing."
        )

    payload = {
        "model": settings.llm_interpreter_model,
        "instructions": _system_prompt(today, base),
        "input": text,
        "text": {"format": _json_schema_format()},
    }
    headers = {
        "Authorization": "Bearer " + settings.openai_api_key.get_secret_value(),
        "Content-Type": "application/json",
    }
    url = settings.openai_base_url.rstrip("/") + "/responses"

    started = time.perf_counter()
    print(
        f"[MOD LLM] start env={environment.upper()} "
        f"model={settings.llm_interpreter_model}"
    )
    try:
        async with httpx.AsyncClient(
            timeout=settings.llm_interpreter_timeout_seconds
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise LLMInterpreterUnavailable(
            "LLM modification interpreter timed out."
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMInterpreterUnavailable(
            "LLM modification interpreter transport error."
        ) from exc

    elapsed = time.perf_counter() - started
    if response.status_code >= 400:
        print(
            f"[MOD LLM] failed status={response.status_code} "
            f"duration={elapsed:.3f}s"
        )
        raise LLMInterpreterUnavailable(
            "LLM modification interpreter returned HTTP "
            f"{response.status_code}."
        )

    print(
        f"[MOD LLM] complete status={response.status_code} "
        f"duration={elapsed:.3f}s"
    )
    try:
        body = response.json()
    except ValueError as exc:
        raise LLMInterpreterUnavailable(
            "LLM modification interpreter returned invalid JSON."
        ) from exc

    content = _extract_output_text(body)
    if content is None:
        raise LLMInterpreterUnavailable(
            "LLM modification interpreter returned no structured content."
        )

    try:
        return LLMQuoteModificationNormalization.model_validate(json.loads(content))
    except (ValueError, TypeError) as exc:
        raise LLMInterpreterUnavailable(
            "LLM modification interpreter returned invalid structured content."
        ) from exc
