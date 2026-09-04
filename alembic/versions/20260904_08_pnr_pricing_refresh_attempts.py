from alembic import op
import sqlalchemy as sa


revision = "20260904_08"
down_revision = "20260904_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "booking_pnr_pricing_refresh_attempts",
        sa.Column(
            "pricing_refresh_attempt_id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("active_booking_id", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.Text(), nullable=False),
        sa.Column("confirmation_id", sa.Text(), nullable=True),
        sa.Column("expected_brand_code", sa.Text(), nullable=False),
        sa.Column("expected_currency", sa.Text(), nullable=False),
        sa.Column("expected_total", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("pricing_authority_id", sa.Integer(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "active_booking_id",
            name="uq_pnr_pricing_refresh_active_booking",
        ),
        sa.UniqueConstraint(
            "client_request_id",
            name="uq_pnr_pricing_refresh_client_request",
        ),
    )
    op.create_index(
        "idx_pnr_pricing_refresh_booking",
        "booking_pnr_pricing_refresh_attempts",
        ["booking_id", "pricing_refresh_attempt_id"],
    )
    op.create_index(
        "idx_pnr_pricing_refresh_status",
        "booking_pnr_pricing_refresh_attempts",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_pnr_pricing_refresh_status",
        table_name="booking_pnr_pricing_refresh_attempts",
    )
    op.drop_index(
        "idx_pnr_pricing_refresh_booking",
        table_name="booking_pnr_pricing_refresh_attempts",
    )
    op.drop_table("booking_pnr_pricing_refresh_attempts")
