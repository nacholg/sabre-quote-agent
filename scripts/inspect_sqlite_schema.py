from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


def default_db_path() -> Path:
    return Path(
        os.getenv("QUOTE_DB_PATH", "data/quotes.db")
    ).expanduser()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspecciona el esquema SQLite actual sin modificarlo."
    )
    parser.add_argument(
        "--path",
        default=str(default_db_path()),
        help="Ruta al quotes.db actual.",
    )
    args = parser.parse_args()

    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"No existe: {path}")

    print(f"SQLite: {path}")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    tables = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()

    for table in tables:
        name = table["name"]
        print()
        print("=" * 72)
        print(f"TABLE: {name}")
        print("=" * 72)

        columns = conn.execute(
            f'PRAGMA table_info("{name}")'
        ).fetchall()

        for column in columns:
            print(
                f'{column["name"]:<28} '
                f'{column["type"]:<16} '
                f'notnull={column["notnull"]} '
                f'default={column["dflt_value"]} '
                f'pk={column["pk"]}'
            )

        count = conn.execute(
            f'SELECT COUNT(*) FROM "{name}"'
        ).fetchone()[0]
        print(f"ROWS: {count}")

        indexes = conn.execute(
            f'PRAGMA index_list("{name}")'
        ).fetchall()

        if indexes:
            print("INDEXES:")
            for index in indexes:
                print(f'  - {index["name"]}')

    conn.close()


if __name__ == "__main__":
    main()
