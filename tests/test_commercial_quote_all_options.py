from unittest.mock import patch

from app.services.commercial_quote_builder import build_commercial_quote
from tests.test_commercial_quote_builder import _record


def test_build_all_options_does_not_require_selection():
    record = _record().model_copy(update={"selected_ranks": []})

    with patch(
        "app.services.commercial_quote_builder.audit_stored_quote_live",
        side_effect=RuntimeError("SOAP unavailable"),
    ):
        quote = build_commercial_quote(record, selected_only=False)

    assert len(quote.options) == 1
    assert quote.options[0].rank == 1


def test_default_builder_still_requires_selection():
    record = _record().model_copy(update={"selected_ranks": []})

    try:
        build_commercial_quote(record)
    except ValueError as exc:
        assert "no tiene opciones seleccionadas" in str(exc)
    else:
        raise AssertionError("Se esperaba ValueError")


def test_all_options_passes_false_to_air_rules():
    record = _record().model_copy(update={"selected_ranks": []})

    with patch(
        "app.services.commercial_quote_builder.audit_stored_quote_live",
        side_effect=RuntimeError("SOAP unavailable"),
    ) as audit:
        build_commercial_quote(record, selected_only=False)

    audit.assert_called_once_with(record, selected_only=False)
