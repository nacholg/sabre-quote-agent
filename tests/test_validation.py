import pytest
from pydantic import ValidationError

from app.models.quote_request import QuoteSearchRequest


def test_rejects_return_before_departure() -> None:
    with pytest.raises(ValidationError):
        QuoteSearchRequest(
            origin="EZE",
            destination="MIA",
            departure_date="2026-09-20",
            return_date="2026-09-19",
        )
