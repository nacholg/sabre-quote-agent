from alembic import op
import sqlalchemy as sa


revision = "20260825_02"
down_revision = "20260820_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_fare_selections",
        sa.Column("quote_id", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("fare_index", sa.Integer(), nullable=False),
        sa.Column("fare_json", sa.Text(), nullable=False),
        sa.Column("selected_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("quote_id", "rank"),
    )


def downgrade() -> None:
    op.drop_table("quote_fare_selections")
