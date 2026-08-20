import os

from sqlalchemy.engine import make_url

from app.db.database import database_url, get_engine


def test_suite_does_not_inherit_database_url():
    assert os.getenv("DATABASE_URL") is None

    get_engine.cache_clear()
    assert make_url(database_url()).get_backend_name() == "sqlite"


def test_test_can_explicitly_opt_into_database_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:password@example.invalid:5432/testdb",
    )
    get_engine.cache_clear()

    url = make_url(database_url())
    assert url.get_backend_name() == "postgresql"
    assert url.database == "testdb"
