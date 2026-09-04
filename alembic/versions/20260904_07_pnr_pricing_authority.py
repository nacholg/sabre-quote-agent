from alembic import op
import sqlalchemy as sa


revision = "20260904_07"
down_revision = "20260901_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "booking_pnr_pricing_authorities",
        sa.Column(
            "pricing_authority_id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("confirmation_id", sa.Text(), nullable=False),
        sa.Column("price_quote_record_numbers_json", sa.Text(), nullable=False),
        sa.Column("brand_code", sa.Text(), nullable=False),
        sa.Column("brand_name", sa.Text(), nullable=True),
        sa.Column("original_total", sa.Text(), nullable=False),
        sa.Column("current_total", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("price_difference", sa.Text(), nullable=False),
        sa.Column("validating_carrier", sa.Text(), nullable=True),
        sa.Column("fare_basis_codes_json", sa.Text(), nullable=False),
        sa.Column("purchase_deadline_raw", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_booking_pnr_pricing_authority_booking",
        "booking_pnr_pricing_authorities",
        ["booking_id", "pricing_authority_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_booking_pnr_pricing_authority_booking",
        table_name="booking_pnr_pricing_authorities",
    )
    op.drop_table("booking_pnr_pricing_authorities")
