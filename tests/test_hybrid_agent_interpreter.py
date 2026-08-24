from datetime import date

import pytest

from app.models.api import AgentQuoteRequest
from app.services.agent_parser import parse_agent_quote
from app.services.hybrid_agent_interpreter import interpret_agent_quote
from app.services.llm_prompt_normalizer import LLMPromptNormalization


TODAY = date(2026, 8, 24)


def test_mex_compact_prompt_is_supported_by_fast_path():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "cotizar eze mex\n"
                "30Oct con regreso 01NOV\n"
                "1 adt\n"
                "Business"
            ),
            execute=False,
        ),
        today=TODAY,
    )

    req = parsed.search_request
    assert parsed.parser == "deterministic-v1"
    assert req.origin == "EZE"
    assert req.destination == "MEX"
    assert str(req.departure_date) == "2026-10-30"
    assert str(req.return_date) == "2026-11-01"
    assert req.adults == 1
    assert req.cabin.value == "BUSINESS"


@pytest.mark.asyncio
async def test_hybrid_fallback_normalizes_then_reuses_parser(monkeypatch):
    import app.services.hybrid_agent_interpreter as hybrid

    monkeypatch.setattr(
        hybrid,
        "llm_fallback_enabled",
        lambda environment: True,
    )

    real_parse = hybrid.parse_agent_quote
    calls = 0

    def controlled_parse(request, *, today=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError(
                "No pude identificar con certeza origen y destino."
            )
        return real_parse(request, today=today)

    monkeypatch.setattr(
        hybrid,
        "parse_agent_quote",
        controlled_parse,
    )

    async def fake_normalize(text, *, today, environment):
        return LLMPromptNormalization(
            canonical_prompt="EZE-MEX 30OCT regreso 01NOV 1 ADT BUSINESS",
            assumptions=[],
            warnings=[],
        )

    monkeypatch.setattr(
        hybrid,
        "normalize_prompt_with_llm",
        fake_normalize,
    )

    parsed = await interpret_agent_quote(
        AgentQuoteRequest(
            text="texto conversacional que requiere normalización",
            environment="cert",
            execute=False,
        ),
        today=TODAY,
    )

    assert calls == 2
    assert parsed.parser == "hybrid-llm-v1"
    assert parsed.search_request.origin == "EZE"
    assert parsed.search_request.destination == "MEX"
    assert str(parsed.search_request.departure_date) == "2026-10-30"
    assert str(parsed.search_request.return_date) == "2026-11-01"
    assert parsed.search_request.cabin.value == "BUSINESS"


@pytest.mark.asyncio
async def test_deterministic_success_never_calls_llm(monkeypatch):
    import app.services.hybrid_agent_interpreter as hybrid

    monkeypatch.setattr(
        hybrid,
        "llm_fallback_enabled",
        lambda environment: True,
    )

    async def must_not_run(*args, **kwargs):
        raise AssertionError("LLM fallback should not run")

    monkeypatch.setattr(
        hybrid,
        "normalize_prompt_with_llm",
        must_not_run,
    )

    parsed = await interpret_agent_quote(
        AgentQuoteRequest(
            text="EZE-MIA del 19 al 30 de septiembre, 1 ADT, BUSINESS",
            execute=False,
        ),
        today=TODAY,
    )

    assert parsed.parser == "deterministic-v1"


@pytest.mark.asyncio
async def test_disabled_llm_preserves_original_validation_error(monkeypatch):
    import app.services.hybrid_agent_interpreter as hybrid

    monkeypatch.setattr(
        hybrid,
        "llm_fallback_enabled",
        lambda environment: False,
    )

    with pytest.raises(ValueError, match="origen y destino"):
        await interpret_agent_quote(
            AgentQuoteRequest(
                text="Cotizame algo barato para septiembre",
                execute=False,
            ),
            today=TODAY,
        )




def test_explicit_route_does_not_treat_adt_as_compact_date():
    parsed = parse_agent_quote(
        AgentQuoteRequest(
            text=(
                "EZE-MIA del 19 al 30 de septiembre, "
                "1 ADT, BUSINESS"
            ),
            execute=False,
        ),
        today=TODAY,
    )

    req = parsed.search_request
    assert req.origin == "EZE"
    assert req.destination == "MIA"
    assert str(req.departure_date) == "2026-09-19"
    assert str(req.return_date) == "2026-09-30"
    assert req.adults == 1
    assert req.cabin.value == "BUSINESS"

def test_responses_api_output_text_extraction():
    from app.services.llm_prompt_normalizer import _extract_output_text

    value = _extract_output_text(
        {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                '{"canonical_prompt":"EZE-MIA 30OCT",'
                                '"assumptions":[],"warnings":[]}'
                            ),
                        }
                    ],
                }
            ]
        }
    )
    assert value is not None


@pytest.mark.asyncio
async def test_return_intent_without_parsed_return_triggers_llm(monkeypatch):
    import app.services.hybrid_agent_interpreter as hybrid

    monkeypatch.setattr(
        hybrid,
        "llm_fallback_enabled",
        lambda environment: True,
    )

    calls = 0

    async def fake_normalize(text, *, today, environment):
        nonlocal calls
        calls += 1
        return LLMPromptNormalization(
            canonical_prompt=(
                "BUE-MEX 30OCT regreso 01NOV "
                "1 ADT BUSINESS"
            ),
            assumptions=[],
            warnings=[],
        )

    monkeypatch.setattr(
        hybrid,
        "normalize_prompt_with_llm",
        fake_normalize,
    )

    parsed = await interpret_agent_quote(
        AgentQuoteRequest(
            text=(
                "Necesito mandar una persona desde Buenos Aires "
                "a Ciudad de México el 30 de octubre y que vuelva "
                "el primero de noviembre. "
                "Quiero que viaje en ejecutiva."
            ),
            environment="cert",
            execute=False,
        ),
        today=TODAY,
    )

    assert calls == 1
    assert parsed.parser == "hybrid-llm-v1"
    assert len(parsed.search_request.legs) == 2
    assert str(parsed.search_request.legs[0].departure_date) == "2026-10-30"
    assert str(parsed.search_request.legs[1].departure_date) == "2026-11-01"


@pytest.mark.asyncio
async def test_return_intent_gap_does_not_silently_quote_one_way(monkeypatch):
    import app.services.hybrid_agent_interpreter as hybrid

    monkeypatch.setattr(
        hybrid,
        "llm_fallback_enabled",
        lambda environment: False,
    )

    with pytest.raises(
        ValueError,
        match="intención de regreso",
    ):
        await interpret_agent_quote(
            AgentQuoteRequest(
                text=(
                    "Buenos Aires a Ciudad de México el 30 de octubre "
                    "y que vuelva el primero de noviembre, ejecutiva"
                ),
                environment="cert",
                execute=False,
            ),
            today=TODAY,
        )


@pytest.mark.asyncio
async def test_llm_usage_is_logged_without_prompt_text(monkeypatch, capsys):
    import app.services.hybrid_agent_interpreter as hybrid

    monkeypatch.setattr(
        hybrid,
        "llm_fallback_enabled",
        lambda environment: True,
    )

    secret_prompt = "PROMPT-QUE-NO-DEBE-APARECER-EN-LOGS"

    real_parse = hybrid.parse_agent_quote
    calls = 0

    def controlled_parse(request, *, today=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("parser failure")
        return real_parse(request, today=today)

    monkeypatch.setattr(
        hybrid,
        "parse_agent_quote",
        controlled_parse,
    )

    async def fake_normalize(*args, **kwargs):
        return LLMPromptNormalization(
            canonical_prompt="EZE-MEX 30OCT 1 ADT BUSINESS",
            assumptions=[],
            warnings=[],
        )

    monkeypatch.setattr(
        hybrid,
        "normalize_prompt_with_llm",
        fake_normalize,
    )

    await interpret_agent_quote(
        AgentQuoteRequest(
            text=secret_prompt,
            environment="cert",
            execute=False,
        ),
        today=TODAY,
    )

    output = capsys.readouterr().out
    assert "[AGENT] llm fallback start" in output
    assert "[AGENT] llm fallback complete" in output
    assert secret_prompt not in output


@pytest.mark.asyncio
async def test_missing_child_age_never_calls_llm(monkeypatch):
    import app.services.hybrid_agent_interpreter as hybrid

    monkeypatch.setattr(
        hybrid,
        "llm_fallback_enabled",
        lambda environment: True,
    )

    async def must_not_run(*args, **kwargs):
        raise AssertionError(
            "LLM must not invent missing child ages"
        )

    monkeypatch.setattr(
        hybrid,
        "normalize_prompt_with_llm",
        must_not_run,
    )

    with pytest.raises(
        ValueError,
        match="Necesito la edad de cada menor",
    ):
        await interpret_agent_quote(
            AgentQuoteRequest(
                text=(
                    "EZE MIA del 19 al 30 de septiembre, "
                    "2 adultos y 2 niños"
                ),
                environment="cert",
                execute=False,
            ),
            today=TODAY,
        )


@pytest.mark.asyncio
async def test_missing_child_age_logs_clarification_not_llm(monkeypatch, capsys):
    import app.services.hybrid_agent_interpreter as hybrid

    monkeypatch.setattr(
        hybrid,
        "llm_fallback_enabled",
        lambda environment: True,
    )

    with pytest.raises(ValueError):
        await interpret_agent_quote(
            AgentQuoteRequest(
                text=(
                    "EZE MIA del 19 al 30 de septiembre, "
                    "2 adultos y 2 niños"
                ),
                environment="cert",
                execute=False,
            ),
            today=TODAY,
        )

    output = capsys.readouterr().out
    assert "clarification required; llm fallback skipped" in output
    assert "[AGENT LLM]" not in output
