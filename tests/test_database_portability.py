from app.db.database import database_url, normalize_database_url


def test_postgres_urls_use_psycopg_driver():
    assert normalize_database_url(
        "postgres://user:pass@host:5432/db"
    ) == "postgresql+psycopg://user:pass@host:5432/db"

    assert normalize_database_url(
        "postgresql://user:pass@host:5432/db"
    ) == "postgresql+psycopg://user:pass@host:5432/db"


def test_local_fallback_uses_quote_db_path(tmp_path, monkeypatch):
    db = tmp_path / "quotes.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("QUOTE_DB_PATH", str(db))

    url = database_url()
    assert url.startswith("sqlite+pysqlite:///")
    assert "quotes.db" in url


def test_database_url_wins_over_quote_db_path(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://u:p@db.internal:5432/app",
    )
    monkeypatch.setenv("QUOTE_DB_PATH", "ignored.db")

    assert database_url() == (
        "postgresql+psycopg://u:p@db.internal:5432/app"
    )
