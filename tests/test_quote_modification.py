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
