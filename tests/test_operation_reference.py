from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.api import QuoteSearchAPIResponse


def _minimal_response(**overrides):
    payload = {
        "environment": "CERT",
        "effective_currencies": [],
        "calls": [],
        "result_count": 0,
        "available_option_count": 0,
        "options": [],
        "client_quote": "",
    }
    payload.update(overrides)
    return QuoteSearchAPIResponse(**payload)


def test_quote_response_exposes_operation_reference():
    response = _minimal_response(operation_id="A1B2C3D4")

    assert response.operation_id == "A1B2C3D4"
    assert response.model_dump(mode="json")["operation_id"] == "A1B2C3D4"


def test_operation_reference_is_optional_for_legacy_quotes():
    response = _minimal_response()

    assert response.operation_id is None


@pytest.mark.parametrize(
    "value",
    [
        "a1b2c3d4",
        "A1B2C3",
        "A1B2C3D4E5",
        "NOTHEX12",
    ],
)
def test_operation_reference_has_stable_safe_format(value):
    with pytest.raises(ValidationError):
        _minimal_response(operation_id=value)


def test_quote_service_attaches_log_correlation_id_to_response():
    source = Path("app/services/quote_service.py").read_text(
        encoding="utf-8"
    )

    assert "operation_id=_operation_id" in source


def test_workspace_displays_operation_reference_for_new_and_stored_quotes():
    source = Path("app/web/index.html").read_text(
        encoding="utf-8"
    )

    assert 'q?.operation_id' in source
    assert 'currentQuote?.operation_id' in source
    assert 'Ref. ${q.operation_id}' in source
    assert 'Ref. ${currentQuote.operation_id}' in source
