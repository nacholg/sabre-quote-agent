from __future__ import annotations

import re
import unicodedata
from datetime import date

from app.models.api import AgentInterpretation, AgentQuoteRequest
from app.services.agent_parser import (
    AgentClarificationRequired,
    parse_agent_quote,
)
from app.services.llm_prompt_normalizer import (
    LLMInterpreterUnavailable,
    LLMPromptNormalization,
    llm_fallback_enabled,
    normalize_prompt_with_llm,
)


_RETURN_INTENT = re.compile(
    r"\b("
    r"regreso|regresar|regresa|regrese|"
    r"vuelta|volver|vuelvo|vuelve|vuelva|"
    r"retorno|retornar|"
    r"return|back"
    r")\b"
)

_EXPLICIT_ONE_WAY = re.compile(
    r"\b("
    r"solo\s+ida|solamente\s+ida|"
    r"one[- ]?way|"
    r"sin\s+regreso|sin\s+vuelta"
    r")\b"
)


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower())
    return "".join(
        ch for ch in value
        if unicodedata.category(ch) != "Mn"
    )


def _semantic_review_reason(
    request: AgentQuoteRequest,
    interpretation: AgentInterpretation,
) -> str | None:
    text = _fold(request.text)

    if _EXPLICIT_ONE_WAY.search(text):
        return None

    search = interpretation.search_request
    has_return_leg = (
        search.return_date is not None
        or len(search.legs) >= 2
    )

    if _RETURN_INTENT.search(text) and not has_return_leg:
        return "return_intent_without_return_date"

    return None


def _agent_log(message: str) -> None:
    print(f"[AGENT] {message}")


async def _normalize_and_reparse(
    request: AgentQuoteRequest,
    *,
    today: date,
    reason: str,
) -> AgentInterpretation:
    _agent_log(
        f"llm fallback start reason={reason} "
        f"env={request.environment.upper()}"
    )

    normalized: LLMPromptNormalization = (
        await normalize_prompt_with_llm(
            request.text,
            today=today,
            environment=request.environment,
        )
    )

    canonical_request = request.model_copy(
        update={"text": normalized.canonical_prompt}
    )

    interpretation = parse_agent_quote(
        canonical_request,
        today=today,
    )
    interpretation.parser = "hybrid-llm-v1"
    interpretation.assumptions = [
        *normalized.assumptions,
        *interpretation.assumptions,
    ]
    interpretation.warnings = [
        *normalized.warnings,
        *interpretation.warnings,
    ]

    _agent_log(
        f"llm fallback complete reason={reason} "
        "parser=hybrid-llm-v1"
    )
    return interpretation


async def interpret_agent_quote(
    request: AgentQuoteRequest,
    *,
    today: date | None = None,
) -> AgentInterpretation:
    """Deterministic first, with LLM fallback for failures or semantic gaps."""
    effective_today = today or date.today()

    try:
        deterministic = parse_agent_quote(
            request,
            today=effective_today,
        )
    except AgentClarificationRequired:
        _agent_log(
            "clarification required; llm fallback skipped"
        )
        raise
    except ValueError as deterministic_error:
        if not llm_fallback_enabled(request.environment):
            raise

        try:
            return await _normalize_and_reparse(
                request,
                today=effective_today,
                reason="deterministic_validation_error",
            )
        except LLMInterpreterUnavailable:
            _agent_log(
                "llm fallback unavailable "
                "reason=deterministic_validation_error"
            )
            raise deterministic_error
        except ValueError as normalized_error:
            raise ValueError(
                f"{deterministic_error} "
                "La interpretación asistida tampoco pudo validar el pedido."
            ) from normalized_error

    review_reason = _semantic_review_reason(
        request,
        deterministic,
    )

    if review_reason is None:
        return deterministic

    _agent_log(
        f"deterministic interpretation requires review "
        f"reason={review_reason}"
    )

    if not llm_fallback_enabled(request.environment):
        raise ValueError(
            "Detecté intención de regreso, pero no pude interpretar "
            "con certeza la fecha o el tramo de vuelta."
        )

    try:
        return await _normalize_and_reparse(
            request,
            today=effective_today,
            reason=review_reason,
        )
    except LLMInterpreterUnavailable as exc:
        _agent_log(
            f"llm fallback unavailable reason={review_reason}"
        )
        raise ValueError(
            "Detecté intención de regreso, pero la interpretación "
            "asistida no está disponible para resolverla con seguridad."
        ) from exc
    except ValueError as normalized_error:
        raise ValueError(
            "Detecté intención de regreso, pero la interpretación "
            "asistida no pudo validar la vuelta con seguridad."
        ) from normalized_error
