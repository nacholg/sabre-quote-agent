import re
from pathlib import Path

from app.services.quote_service import (
    _new_operation_id,
    _quote_log,
)


def test_operation_id_is_short_uppercase_hex():
    operation_id = _new_operation_id()

    assert re.fullmatch(r"[0-9A-F]{8}", operation_id)


def test_quote_log_adds_correlation_prefix(capsys):
    _quote_log("A1B2C3D4", "BFM batch complete")

    assert (
        capsys.readouterr().out.strip()
        == "[QUOTE A1B2C3D4] BFM batch complete"
    )


def test_quote_service_logging_is_database_agnostic():
    source = Path("app/services/quote_service.py").read_text(
        encoding="utf-8"
    )

    assert "[QUOTE] SQLite" not in source
    assert "SQLite=" not in source
    assert "repository.dialect_name" in source
    assert "DB {_db_dialect} write" in source
    assert "DB write={_persist_seconds:.3f}s ({_db_dialect})" in source


def test_quote_service_correlates_operational_log_lines():
    source = Path("app/services/quote_service.py").read_text(
        encoding="utf-8"
    )

    assert "_operation_id = _new_operation_id()" in source
    assert source.count("_quote_log(") >= 8
    assert "start env=" in source
    assert "complete total=" in source


def test_operational_logs_do_not_reference_database_connection_secrets():
    source = Path("app/services/quote_service.py").read_text(
        encoding="utf-8"
    )

    forbidden = (
        "DATABASE_URL",
        "password=",
        "postgresql://",
        "postgres://",
    )
    for token in forbidden:
        assert token not in source
