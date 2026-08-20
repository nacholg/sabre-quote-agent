from __future__ import annotations

from sqlalchemy import inspect

from app.db.database import get_engine
from app.db.models import QuoteArtifactRow, QuoteRow


EXPECTED = {
    "quotes": {
        "columns": [column.name for column in QuoteRow.__table__.columns],
        "indexes": {"idx_quotes_created_at"},
    },
    "quote_artifacts": {
        "columns": [
            column.name
            for column in QuoteArtifactRow.__table__.columns
        ],
        "indexes": {"idx_quote_artifacts_quote_created"},
    },
}


def main() -> None:
    inspector = inspect(get_engine())
    ok = True

    for table_name, expected in EXPECTED.items():
        print()
        print(f"TABLE: {table_name}")

        actual_columns = [
            item["name"]
            for item in inspector.get_columns(table_name)
        ]
        actual_indexes = {
            item["name"]
            for item in inspector.get_indexes(table_name)
        }

        if actual_columns == expected["columns"]:
            print("Columns: OK")
        else:
            ok = False
            print("Columns: DIFFER")
            print("Expected:", expected["columns"])
            print("Actual:  ", actual_columns)

        if actual_indexes == expected["indexes"]:
            print("Indexes: OK")
        else:
            ok = False
            print("Indexes: DIFFER")
            print("Expected:", sorted(expected["indexes"]))
            print("Actual:  ", sorted(actual_indexes))

    if not ok:
        raise SystemExit(1)

    print()
    print("Schema verification: OK")


if __name__ == "__main__":
    main()
