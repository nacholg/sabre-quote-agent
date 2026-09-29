from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.models.secure_flight import (
    SecureFlightDocumentInput,
    SecureFlightDocumentSummary,
)


_SECRET = "X1234567"


def _document(**overrides) -> SecureFlightDocumentInput:
    payload = {
        "document_type": "P",
        "document_number": _SECRET,
        "issuing_country": "ar",
        "nationality": "ar",
        "expiry_date": "2030-12-31",
    }
    payload.update(overrides)
    return SecureFlightDocumentInput.model_validate(payload)


def test_secure_flight_document_is_transient_and_normalized() -> None:
    document = _document()

    assert document.document_type == "P"
    assert document.issuing_country == "AR"
    assert document.nationality == "AR"
    assert document.expiry_date == date(2030, 12, 31)
    assert document.document_number_value() == _SECRET


def test_document_number_is_absent_from_repr_and_string() -> None:
    document = _document()

    assert _SECRET not in repr(document)
    assert _SECRET not in str(document)
    assert "document_number=" not in repr(document)


def test_document_number_is_excluded_from_all_model_serialization() -> None:
    document = _document()

    dumped = document.model_dump()
    dumped_json = document.model_dump_json()

    assert "document_number" not in dumped
    assert "document_number" not in dumped_json
    assert _SECRET not in str(dumped)
    assert _SECRET not in dumped_json


def test_redacted_summary_exposes_presence_but_never_number() -> None:
    document = _document()

    summary = document.redacted_summary()

    assert isinstance(summary, SecureFlightDocumentSummary)
    assert summary.document_number_present is True
    assert summary.document_type == "P"
    assert summary.issuing_country == "AR"
    assert summary.nationality == "AR"
    assert summary.expiry_date == date(2030, 12, 31)
    assert "document_number" not in summary.model_dump()
    assert _SECRET not in summary.model_dump_json()


def test_unrelated_validation_error_does_not_echo_document_number() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _document(issuing_country="ARG")

    rendered = str(exc_info.value)
    serialized = exc_info.value.json()

    assert _SECRET not in rendered
    assert _SECRET not in serialized


def test_blank_document_number_fails_after_secret_wrapping() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _document(document_number="   ")

    rendered = str(exc_info.value)

    assert "Document number es obligatorio" in rendered
    assert "input_value" not in rendered


def test_extra_fields_are_rejected_fail_closed() -> None:
    with pytest.raises(ValidationError):
        _document(raw_document_number=_SECRET)
