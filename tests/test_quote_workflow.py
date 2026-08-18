from decimal import Decimal

from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse
from app.services.quote_repository import QuoteRepository


def _quote(repo: QuoteRepository) -> str:
    request=QuoteSearchAPIRequest(
        origin="EZE",destination="MIA",departure_date="2026-09-19",
        return_date="2026-09-30",persist=False,
    )
    response=QuoteSearchAPIResponse(
        environment="CERT",effective_currencies=["USD"],calls=[],
        result_count=0,options=[],client_quote="TEST",
    )
    return repo.create(request=request,response=response)


def test_workflow_metadata_and_sent_status(tmp_path):
    repo=QuoteRepository(tmp_path/"quotes.db")
    quote_id=_quote(repo)
    updated=repo.update_workflow(
        quote_id,
        client_name="Cliente Uno",
        client_reference="REF-123",
        notes="Llamar mañana",
        status="sent",
    )
    assert updated.status == "sent"
    assert updated.client_name == "Cliente Uno"
    assert updated.client_reference == "REF-123"
    assert updated.sent_at is not None
    record=repo.get(quote_id)
    assert record.notes == "Llamar mañana"


def test_refresh_link_marks_original_superseded(tmp_path):
    repo=QuoteRepository(tmp_path/"quotes.db")
    old_id=_quote(repo)
    new_id=_quote(repo)
    repo.link_refresh(old_id,new_id)
    old=repo.get(old_id)
    assert old.status == "superseded"
    assert old.refreshed_quote_id == new_id
