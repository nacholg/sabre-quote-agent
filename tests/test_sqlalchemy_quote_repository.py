from pathlib import Path

from app.services.quote_repository import QuoteRepository


def test_repository_uses_sqlalchemy_engine_for_sqlite(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")

    assert repo.dialect_name == "sqlite"
    assert repo.engine.dialect.name == "sqlite"
    assert repo.db_path == tmp_path / "quotes.db"

    repo.close()


def test_artifact_operations_do_not_depend_on_legacy_connect(tmp_path):
    repo = QuoteRepository(tmp_path / "quotes.db")

    def fail_if_called():
        raise AssertionError(
            "Repository operation used legacy sqlite3 _connect()."
        )

    repo._connect = fail_if_called  # type: ignore[method-assign]

    created = repo.create_artifact(
        "Q-PORTABLE-1",
        artifact_type="whatsapp",
        title="WhatsApp",
        selected_ranks=[2, 1, 2],
        content_type="text/plain",
        content="Hola",
    )

    rows = repo.list_artifacts("Q-PORTABLE-1")

    assert len(rows) == 1
    assert rows[0]["artifact_id"] == created["artifact_id"]
    assert rows[0]["selected_ranks"] == [1, 2]

    assert repo.delete_artifact(
        "Q-PORTABLE-1",
        created["artifact_id"],
    )
    assert repo.list_artifacts("Q-PORTABLE-1") == []

    repo.close()


def test_repository_without_explicit_path_uses_database_url(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "env-quotes.db"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("QUOTE_DB_PATH", str(db_path))

    repo = QuoteRepository()

    assert repo.dialect_name == "sqlite"
    assert repo.db_path == db_path
    assert "sqlite+pysqlite" in repo.database_url

    repo.close()


def test_repository_source_no_longer_uses_sqlite_queries_for_operations():
    source = Path(
        "app/services/quote_repository.py"
    ).read_text(encoding="utf-8")

    assert "insert(QUOTE_TABLE)" in source
    assert "select(QUOTE_TABLE)" in source
    assert "update(QUOTE_TABLE)" in source
    assert "insert(ARTIFACT_TABLE)" in source
    assert "delete(ARTIFACT_TABLE)" in source
    assert "with self._connect() as conn:" not in source
