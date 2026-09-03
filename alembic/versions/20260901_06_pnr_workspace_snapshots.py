from alembic import op
import sqlalchemy as sa


revision = "20260901_06"
down_revision = "20260827_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "booking_pnr_snapshots",
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("confirmation_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("booking_id"),
    )
    op.create_index(
        "idx_booking_pnr_snapshots_confirmation",
        "booking_pnr_snapshots",
        ["confirmation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_booking_pnr_snapshots_confirmation",
        table_name="booking_pnr_snapshots",
    )
    op.drop_table("booking_pnr_snapshots")
