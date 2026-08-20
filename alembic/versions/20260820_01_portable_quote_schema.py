from alembic import op
import sqlalchemy as sa


revision = "20260820_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quotes",
        sa.Column("quote_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("agent_text", sa.Text(), nullable=True),
        sa.Column("interpretation_json", sa.Text(), nullable=True),
        sa.Column("search_request_json", sa.Text(), nullable=False),
        sa.Column("quote_response_json", sa.Text(), nullable=False),
        sa.Column(
            "selected_ranks_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("client_name", sa.Text(), nullable=True),
        sa.Column("client_reference", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.Text(), nullable=True),
        sa.Column("parent_quote_id", sa.Text(), nullable=True),
        sa.Column("refreshed_quote_id", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_quotes_created_at",
        "quotes",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "quote_artifacts",
        sa.Column(
            "artifact_id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("quote_id", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "selected_ranks_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_quote_artifacts_quote_created",
        "quote_artifacts",
        ["quote_id", "artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_quote_artifacts_quote_created",
        table_name="quote_artifacts",
    )
    op.drop_table("quote_artifacts")

    op.drop_index(
        "idx_quotes_created_at",
        table_name="quotes",
    )
    op.drop_table("quotes")
