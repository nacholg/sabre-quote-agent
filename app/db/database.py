from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def normalize_database_url(value: str) -> str:
    value = value.strip()

    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]

    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]

    return value


def database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        return normalize_database_url(configured)

    quote_db_path = Path(
        os.getenv("QUOTE_DB_PATH", "data/quotes.db")
    ).expanduser()

    if not quote_db_path.is_absolute():
        quote_db_path = (Path.cwd() / quote_db_path).resolve()

    quote_db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{quote_db_path.as_posix()}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = database_url()
    kwargs: dict = {"pool_pre_ping": True}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}

    return create_engine(url, **kwargs)


def reset_engine_for_tests() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
