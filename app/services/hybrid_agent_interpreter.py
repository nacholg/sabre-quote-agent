from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Literal

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


_YEAR_OMISSION_WARNING = re.compile(
    r"(?:"
    r"no\s+se\s+indico|"
    r"no\s+se\s+especifico|"
    r"sin\s+indicar|"
    r"sin\s+especificar|"
    r"falta|"
    r"faltante|"
    r"omitido|"
    r"omitida"
    r").{0,36}\bano\b"
    r"|"
    r"\bano\b.{0,36}(?:"
    r"no\s+indicado|"
    r"no\s+especificado|"
    r"faltante|"
    r"omitido"
    r")"
)


ParserFailurePolicy = Literal[
    "clarification",
    "llm_recoverable",
]


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


def _parser_failure_policy(
    error: ValueError,
) -> ParserFailurePolicy:
    if isinstance(error, AgentClarificationRequired):
        return "clarification"
    return "llm_recoverable"


def _dedupe_messages(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        clean = str(value).strip()
        if not clean:
            continue
        key = _fold(clean)
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)

    return result


def _interpreted_years(
    interpretation: AgentInterpretation,
) -> list[int]:
    request = interpretation.search_request
    years: set[int] = set()

    for leg in request.legs:
        years.add(leg.departure_date.year)

    if request.departure_date is not None:
        years.add(request.departure_date.year)
    if request.return_date is not None:
        years.add(request.return_date.year)

    return sorted(years)


def _merge_llm_messages(
    normalized: LLMPromptNormalization,
    interpretation: AgentInterpretation,
) -> tuple[list[str], list[str]]:
    assumptions = list(normalized.assumptions)
    warnings: list[str] = []

    years = _interpreted_years(interpretation)
    inferred_year_message: str | None = None

    if len(years) == 1:
        inferred_year_message = (
            f"Año inferido para las fechas: {years[0]}."
        )
    elif len(years) > 1:
        inferred_year_message = (
            "Años inferidos para las fechas: "
            + ", ".join(str(year) for year in years)
            + "."
        )

    # Apply the same message policy to both sources:
    # the LLM normalizer and the deterministic reparse.
    for warning in [
        *normalized.warnings,
        *interpretation.warnings,
    ]:
        folded = _fold(warning)
        if (
            inferred_year_message
            and _YEAR_OMISSION_WARNING.search(folded)
        ):
            assumptions.append(inferred_year_message)
            continue
        warnings.append(warning)

    assumptions.extend(interpretation.assumptions)

    return (
        _dedupe_messages(assumptions),
        _dedupe_messages(warnings),
    )


def _clarification_message(
    missing_fields: list[str],
) -> str:
    labels = {
        "origin": "el origen",
        "destination": "el destino",
        "departure_date": "la fecha de salida",
        "return_date": "la fecha de regreso",
        "child_ages": "la edad de cada menor",
    }
    readable = [
        labels.get(field, field)
        for field in missing_fields
    ]

    if len(readable) == 1:
        detail = readable[0]
    else:
        detail = ", ".join(readable[:-1]) + " y " + readable[-1]

    return (
        "Necesito que indiques "
        f"{detail} para poder cotizar sin asumir datos."
    )


def _validate_llm_result(
    request: AgentQuoteRequest,
    normalized: LLMPromptNormalization,
    interpretation: AgentInterpretation | None = None,
) -> None:
    if normalized.missing_fields:
        fields = sorted(set(normalized.missing_fields))
        _agent_log(
            "llm clarification required fields="
            + ",".join(fields)
        )
        raise AgentClarificationRequired(
            _clarification_message(fields)
        )

    if interpretation is None:
        return

    review_reason = _semantic_review_reason(
        request,
        interpretation,
    )
    if review_reason == "return_intent_without_return_date":
        _agent_log(
            "llm result rejected "
            "reason=return_intent_without_return_date"
        )
        raise AgentClarificationRequired(
            "Detecté intención de regreso, pero todavía necesito "
            "una fecha o un tramo de vuelta inequívoco para cotizar."
        )


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

    _validate_llm_result(
        request,
        normalized,
    )

    canonical_request = request.model_copy(
        update={"text": normalized.canonical_prompt}
    )

    interpretation = parse_agent_quote(
        canonical_request,
        today=today,
    )

    _validate_llm_result(
        request,
        normalized,
        interpretation,
    )
    interpretation.parser = "hybrid-llm-v1"
    (
        interpretation.assumptions,
        interpretation.warnings,
    ) = _merge_llm_messages(
        normalized,
        interpretation,
    )

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
    except ValueError as deterministic_error:
        failure_policy = _parser_failure_policy(
            deterministic_error
        )

        if failure_policy == "clarification":
            _agent_log(
                "clarification required; llm fallback skipped; "
                "parser failure policy=clarification"
            )
            raise

        _agent_log(
            "parser failure policy=llm_recoverable"
        )

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
        except AgentClarificationRequired:
            raise
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
    except AgentClarificationRequired:
        raise
    except ValueError as normalized_error:
        raise ValueError(
            "Detecté intención de regreso, pero la interpretación "
            "asistida no pudo validar la vuelta con seguridad."
        ) from normalized_error
