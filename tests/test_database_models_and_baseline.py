from pathlib import Path

from app.db.models import QuoteArtifactRow, QuoteFareSelectionRow, QuoteRow


def test_quote_model_matches_real_sqlite_columns():
    assert [c.name for c in QuoteRow.__table__.columns] == [
        "quote_id",
        "created_at",
        "updated_at",
        "status",
        "source",
        "agent_text",
        "interpretation_json",
        "search_request_json",
        "quote_response_json",
        "selected_ranks_json",
        "client_name",
        "client_reference",
        "notes",
        "sent_at",
        "parent_quote_id",
        "refreshed_quote_id",
    ]


def test_fare_selection_model_columns():
    assert [c.name for c in QuoteFareSelectionRow.__table__.columns] == [
        "quote_id",
        "rank",
        "fare_index",
        "fare_json",
        "selected_at",
    ]


def test_artifact_model_matches_real_sqlite_columns():
    assert [c.name for c in QuoteArtifactRow.__table__.columns] == [
        "artifact_id",
        "quote_id",
        "artifact_type",
        "title",
        "selected_ranks_json",
        "content_type",
        "content",
        "created_at",
    ]


def test_index_names_match_real_sqlite():
    assert {
        index.name
        for index in QuoteRow.__table__.indexes
    } == {"idx_quotes_created_at"}

    assert {
        index.name
        for index in QuoteArtifactRow.__table__.indexes
    } == {"idx_quote_artifacts_quote_created"}


def test_alembic_files_exist():
    assert Path("alembic/env.py").exists()
    assert Path(
        "alembic/versions/20260820_01_portable_quote_schema.py"
    ).exists()
    assert Path(
        "alembic/versions/20260825_02_quote_fare_selections.py"
    ).exists()
