from alembic import op
import sqlalchemy as sa


revision = "20260826_03"
down_revision = "20260825_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_booking_drafts",
        sa.Column("quote_id", sa.Text(), nullable=False),
        sa.Column(
            "passengers_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "contact_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("received_from", sa.Text(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("quote_id"),
    )


def downgrade() -> None:
    op.drop_table("quote_booking_drafts")
