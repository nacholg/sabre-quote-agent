from __future__ import annotations

import pytest

from app.models.api import (
    QuoteModificationRequest,
    QuoteSearchAPIRequest,
    QuoteSearchAPIResponse,
)
from app.models.quote_request import Cabin
from app.services.quote_modification import modify_stored_quote
from app.services.quote_repository import QuoteRepository


def _base_request() -> QuoteSearchAPIRequest:
    return QuoteSearchAPIRequest(
        environment="cert",
        origin="BUE",
        destination="MEX",
        departure_date="2026-10-30",
        return_date="2026-11-01",
        adults=1,
        cabin=Cabin.BUSINESS,
        persist=True,
    )


def _empty_response() -> QuoteSearchAPIResponse:
    return QuoteSearchAPIResponse(
        environment="cert",
        effective_currencies=["USD"],
        calls=[],
        result_count=0,
        available_option_count=0,
        options=[],
        client_quote="",
    )


def _seed(repo: QuoteRepository) -> str:
    return repo.create(
        request=_base_request(),
        response=_empty_response(),
        source="agent",
        agent_text="BUE-MEX 30OCT regreso 01NOV 1 ADT BUSINESS",
    )


@pytest.mark.asyncio
async def test_conversational_preview_changes_adults_without_persisting(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="cotizar para 2 personas",
            execute=False,
        ),
    )

    assert response.base_quote_id == quote_id
    assert response.new_quote_id is None
    assert response.search_request.adults == 2
    assert response.search_request.cabin == Cabin.BUSINESS
    assert response.search_request.origin == "BUE"
    assert response.search_request.destination == "MEX"

    history = repo.version_history(quote_id)
    assert history.total_versions == 1


@pytest.mark.asyncio
async def test_conversational_preview_can_change_cabin(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="probalo en economy",
            execute=False,
        ),
    )

    assert response.search_request.cabin == Cabin.ECONOMY
    assert [item.field for item in response.changes] == ["cabin"]


@pytest.mark.asyncio
async def test_conversational_preview_rejects_unknown_change(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    with pytest.raises(ValueError, match="cambio concreto"):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(
                text="buscame algo mejor",
                execute=False,
            ),
        )


@pytest.mark.asyncio
async def test_conversational_execution_creates_new_version(
    tmp_path,
    monkeypatch,
):
    import app.services.quote_modification as modification

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    async def fake_search(request):
        assert request.persist is False
        assert request.adults == 2
        return _empty_response()

    monkeypatch.setattr(
        modification,
        "search_quote",
        fake_search,
    )

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="cotizar para 2 personas",
            execute=True,
        ),
    )

    assert response.new_quote_id
    assert response.quote is not None
    assert response.quote.quote_id == response.new_quote_id

    old = repo.get(quote_id)
    new = repo.get(response.new_quote_id)
    assert old is not None
    assert new is not None
    assert old.status == "superseded"
    assert old.refreshed_quote_id == new.quote_id
    assert new.parent_quote_id == quote_id
    assert new.source == "agent_modify"

    history = repo.version_history(new.quote_id)
    assert history.total_versions == 2
    assert history.latest_quote_id == new.quote_id


@pytest.mark.asyncio
async def test_conversational_preview_changes_return_date(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="volvé el 3 de noviembre",
            execute=False,
        ),
    )

    assert response.search_request.return_date.isoformat() == "2026-11-03"
    assert response.search_request.departure_date.isoformat() == "2026-10-30"
    assert [item.field for item in response.changes] == ["return_date"]


@pytest.mark.asyncio
async def test_conversational_preview_changes_departure_relative(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="salir un día antes",
            execute=False,
        ),
    )

    assert response.search_request.departure_date.isoformat() == "2026-10-29"
    assert response.search_request.return_date.isoformat() == "2026-11-01"


@pytest.mark.asyncio
async def test_conversational_preview_rejects_ambiguous_roundtrip_date(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    with pytest.raises(ValueError, match="salida o el regreso"):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(
                text="el 3 de noviembre",
                execute=False,
            ),
        )


@pytest.mark.asyncio
async def test_conversational_preview_replaces_included_carrier(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="solo AM",
            execute=False,
        ),
    )

    assert response.search_request.carriers == ["AM"]
    assert [item.field for item in response.changes] == ["carriers"]


@pytest.mark.asyncio
async def test_conversational_preview_adds_excluded_carrier(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="excluir AV",
            execute=False,
        ),
    )

    assert response.search_request.excluded_carriers == ["AV"]
    assert [item.field for item in response.changes] == ["excluded_carriers"]


@pytest.mark.asyncio
async def test_conversational_preview_changes_fare_preference_to_baggage(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="con equipaje",
            execute=False,
        ),
    )

    assert response.search_request.fare_preference.value == "baggage"
    assert [item.field for item in response.changes] == ["fare_preference"]


@pytest.mark.asyncio
async def test_conversational_preview_changes_fare_preference_to_refundable(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="que sea refundable",
            execute=False,
        ),
    )

    assert response.search_request.fare_preference.value == "refundable"


@pytest.mark.asyncio
async def test_conversational_hybrid_fallback_normalizes_free_language(tmp_path, monkeypatch):
    import app.services.quote_modification as modification
    from app.services.llm_quote_modification_normalizer import LLMQuoteModificationNormalization

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)
    monkeypatch.setattr(modification, "llm_fallback_enabled", lambda environment: True)

    async def fake_normalize(*args, **kwargs):
        return LLMQuoteModificationNormalization(
            canonical_instruction="volver el 03NOV2026",
            assumptions=["'un par de días' interpretado como 2 días."],
            warnings=[],
            needs_clarification=False,
            clarification=None,
        )

    monkeypatch.setattr(modification, "normalize_quote_modification_with_llm", fake_normalize)

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(text="corramos la vuelta un par de días", execute=False),
    )

    assert response.parser == "conversation-hybrid-llm-v1"
    assert response.search_request.return_date.isoformat() == "2026-11-03"
    assert response.assumptions == ["'un par de días' interpretado como 2 días."]


@pytest.mark.asyncio
async def test_conversational_vague_request_requires_clarification_without_llm(tmp_path, monkeypatch):
    import app.services.quote_modification as modification

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)
    called = False

    async def fake_normalize(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM no debe ejecutarse")

    monkeypatch.setattr(modification, "llm_fallback_enabled", lambda environment: True)
    monkeypatch.setattr(modification, "normalize_quote_modification_with_llm", fake_normalize)

    with pytest.raises(ValueError, match="cambio concreto"):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(text="buscame algo mejor", execute=False),
        )

    assert called is False


@pytest.mark.asyncio
async def test_conversational_llm_can_request_clarification(tmp_path, monkeypatch):
    import app.services.quote_modification as modification
    from app.services.llm_quote_modification_normalizer import LLMQuoteModificationNormalization

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)
    monkeypatch.setattr(modification, "llm_fallback_enabled", lambda environment: True)

    async def fake_normalize(*args, **kwargs):
        return LLMQuoteModificationNormalization(
            canonical_instruction="NO_CHANGE",
            assumptions=[],
            warnings=[],
            needs_clarification=True,
            clarification="¿Querés priorizar precio, duración o menos escalas?",
        )

    monkeypatch.setattr(modification, "normalize_quote_modification_with_llm", fake_normalize)

    with pytest.raises(ValueError, match="priorizar"):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(text="haceme una alternativa más conveniente", execute=False),
        )


@pytest.mark.asyncio
async def test_conversational_llm_unavailable_preserves_local_error(tmp_path, monkeypatch):
    import app.services.quote_modification as modification
    from app.services.llm_prompt_normalizer import LLMInterpreterUnavailable

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)
    monkeypatch.setattr(modification, "llm_fallback_enabled", lambda environment: True)

    async def unavailable(*args, **kwargs):
        raise LLMInterpreterUnavailable("offline")

    monkeypatch.setattr(modification, "normalize_quote_modification_with_llm", unavailable)

    with pytest.raises(ValueError, match="cambio concreto"):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(text="ajustemos un poco la cotización", execute=False),
        )


@pytest.mark.asyncio
async def test_conversational_llm_logs_do_not_echo_user_text(tmp_path, monkeypatch, capsys):
    import app.services.quote_modification as modification
    from app.services.llm_quote_modification_normalizer import LLMQuoteModificationNormalization

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)
    secret_text = "PROMPT-MODIFICACION-SENSIBLE"
    monkeypatch.setattr(modification, "llm_fallback_enabled", lambda environment: True)

    async def fake_normalize(*args, **kwargs):
        return LLMQuoteModificationNormalization(
            canonical_instruction="probalo en ECONOMY",
            assumptions=[],
            warnings=[],
            needs_clarification=False,
            clarification=None,
        )

    monkeypatch.setattr(modification, "normalize_quote_modification_with_llm", fake_normalize)

    await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(text=secret_text, execute=False),
    )

    output = capsys.readouterr().out
    assert "llm fallback start" in output
    assert secret_text not in output


@pytest.mark.asyncio
async def test_conversational_combined_local_changes_are_atomic(tmp_path, monkeypatch):
    import app.services.quote_modification as modification

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    called = False

    async def fake_normalize(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM no debe ejecutarse para cambios locales soportados")

    monkeypatch.setattr(
        modification,
        "llm_fallback_enabled",
        lambda environment: True,
    )
    monkeypatch.setattr(
        modification,
        "normalize_quote_modification_with_llm",
        fake_normalize,
    )

    response = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text=(
                "2 personas, economy, volver el 5 de noviembre, "
                "solo directos y con equipaje"
            ),
            execute=False,
        ),
    )

    assert response.parser == "conversation-delta-v1"
    assert response.search_request.adults == 2
    assert response.search_request.cabin == Cabin.ECONOMY
    assert response.search_request.return_date.isoformat() == "2026-11-05"
    assert response.search_request.direct is True
    assert response.search_request.max_stops == 0
    assert response.search_request.fare_preference.value == "baggage"
    assert called is False

    fields = {item.field for item in response.changes}
    assert {
        "passengers",
        "cabin",
        "max_stops",
        "return_date",
        "fare_preference",
    }.issubset(fields)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("cambiá el destino a Madrid", "origen, destino"),
        ("ahora salir desde Córdoba", "origen, destino"),
        ("agregá un tramo a Barcelona", "tramos"),
    ],
)
async def test_conversational_route_shape_changes_are_blocked_before_llm(
    tmp_path,
    monkeypatch,
    text,
    message,
):
    import app.services.quote_modification as modification

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    called = False

    async def fake_normalize(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM no debe intentar reescribir la ruta")

    monkeypatch.setattr(
        modification,
        "llm_fallback_enabled",
        lambda environment: True,
    )
    monkeypatch.setattr(
        modification,
        "normalize_quote_modification_with_llm",
        fake_normalize,
    )

    with pytest.raises(ValueError, match=message):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(
                text=text,
                execute=False,
            ),
        )

    assert called is False


@pytest.mark.asyncio
async def test_conversational_minor_passenger_change_is_never_partially_applied(
    tmp_path,
    monkeypatch,
):
    import app.services.quote_modification as modification

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    called = False

    async def fake_normalize(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM no debe inventar composición de menores")

    monkeypatch.setattr(
        modification,
        "llm_fallback_enabled",
        lambda environment: True,
    )
    monkeypatch.setattr(
        modification,
        "normalize_quote_modification_with_llm",
        fake_normalize,
    )

    with pytest.raises(ValueError, match="menores"):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(
                text="ahora 2 adultos y 1 niño de 9 años",
                execute=False,
            ),
        )

    assert called is False

    record = repo.get(quote_id)
    assert record is not None
    stored = QuoteSearchAPIRequest.model_validate(record.search_request)
    assert stored.adults == 1


@pytest.mark.asyncio
async def test_conversational_relative_passenger_change_requires_explicit_total(
    tmp_path,
    monkeypatch,
):
    import app.services.quote_modification as modification

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    monkeypatch.setattr(
        modification,
        "llm_fallback_enabled",
        lambda environment: True,
    )

    with pytest.raises(ValueError, match="nuevo total"):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(
                text="agregá un adulto",
                execute=False,
            ),
        )


@pytest.mark.asyncio
async def test_historical_version_cannot_be_modified(tmp_path, monkeypatch):
    import app.services.quote_modification as modification
    from app.services.quote_repository import QuoteVersionConflictError

    repo = QuoteRepository(tmp_path / "quotes.db")
    quote_id = _seed(repo)

    async def fake_search(request):
        return _empty_response()

    monkeypatch.setattr(
        modification,
        "search_quote",
        fake_search,
    )

    first = await modify_stored_quote(
        repo,
        quote_id,
        QuoteModificationRequest(
            text="cotizar para 2 personas",
            execute=True,
        ),
    )
    assert first.new_quote_id

    with pytest.raises(QuoteVersionConflictError, match="histórica"):
        await modify_stored_quote(
            repo,
            quote_id,
            QuoteModificationRequest(
                text="probalo en economy",
                execute=False,
            ),
        )


@pytest.mark.asyncio
async def test_conversational_version_chain_preserves_followup_context(
    tmp_path,
    monkeypatch,
):
    import app.services.quote_modification as modification

    repo = QuoteRepository(tmp_path / "quotes.db")
    v1 = _seed(repo)

    async def fake_search(request):
        return _empty_response()

    monkeypatch.setattr(
        modification,
        "search_quote",
        fake_search,
    )

    first_text = "cotizar para 2 personas"
    first = await modify_stored_quote(
        repo,
        v1,
        QuoteModificationRequest(
            text=first_text,
            execute=True,
        ),
    )
    v2 = first.new_quote_id
    assert v2

    second_text = "probalo en economy"
    second = await modify_stored_quote(
        repo,
        v2,
        QuoteModificationRequest(
            text=second_text,
            execute=True,
        ),
    )
    v3 = second.new_quote_id
    assert v3

    rec2 = repo.get(v2)
    rec3 = repo.get(v3)
    assert rec2 is not None
    assert rec3 is not None

    assert rec2.parent_quote_id == v1
    assert rec3.parent_quote_id == v2
    assert rec2.agent_text == first_text
    assert rec3.agent_text == second_text
    assert rec2.interpretation["parser"] == "conversation-delta-v1"
    assert rec3.interpretation["parser"] == "conversation-delta-v1"

    history = repo.version_history(v3)
    assert history.total_versions == 3
    assert history.latest_quote_id == v3
