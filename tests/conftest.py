
# --- test database isolation: DATABASE_URL ---
# Prevent a DATABASE_URL inherited from the developer shell from making the
# normal test suite talk to a real/external database. Individual tests may
# still opt in by setting DATABASE_URL explicitly with monkeypatch.
import os as _dbiso_os

import pytest as _dbiso_pytest

from app.db.database import get_engine as _dbiso_get_engine


_dbiso_os.environ.pop("DATABASE_URL", None)
_dbiso_get_engine.cache_clear()


@_dbiso_pytest.fixture(autouse=True)
def _isolate_database_url(monkeypatch):
    """Start every test without an inherited DATABASE_URL."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _dbiso_get_engine.cache_clear()

    yield

    # Avoid a cached engine created by a test leaking into the next test.
    _dbiso_get_engine.cache_clear()
