from __future__ import annotations

from sqlalchemy import text

from app.db.database import database_url, get_engine


def safe_url(url: str) -> str:
    if "@" not in url:
        return url

    prefix, suffix = url.rsplit("@", 1)
    if "://" not in prefix:
        return url

    scheme = prefix.split("://", 1)[0]
    return f"{scheme}://***:***@{suffix}"


def main() -> None:
    url = database_url()
    print("DATABASE_URL:", safe_url(url))

    engine = get_engine()
    print("Dialect:", engine.dialect.name)

    with engine.connect() as connection:
        value = connection.execute(text("SELECT 1")).scalar_one()

    print("Connection test:", value)


if __name__ == "__main__":
    main()
