from alembic import op
import sqlalchemy as sa


revision = "20260827_05"
down_revision = "20260826_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "booking_pnr_attempts",
        sa.Column(
            "pnr_attempt_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("client_request_id", sa.Text(), nullable=False),
        sa.Column("booking_revision", sa.Integer(), nullable=False),
        sa.Column(
            "accepted_offer_revision_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("revalidation_id", sa.Integer(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confirmation_id", sa.Text(), nullable=True),
        sa.Column("provider_reference", sa.Text(), nullable=True),
        sa.Column("request_fingerprint", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("pnr_attempt_id"),
        sa.UniqueConstraint(
            "booking_id",
            name="uq_booking_pnr_attempt_booking",
        ),
        sa.UniqueConstraint(
            "client_request_id",
            name="uq_booking_pnr_attempt_client_request",
        ),
    )
    op.create_index(
        "idx_booking_pnr_attempts_status",
        "booking_pnr_attempts",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_booking_pnr_attempts_status",
        table_name="booking_pnr_attempts",
    )
    op.drop_table("booking_pnr_attempts")
