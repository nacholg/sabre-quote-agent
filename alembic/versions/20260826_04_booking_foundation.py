from alembic import op
import sqlalchemy as sa


revision = "20260826_04"
down_revision = "20260826_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("source_quote_id", sa.Text(), nullable=False),
        sa.Column("selected_rank", sa.Integer(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "revalidation_status",
            sa.Text(),
            nullable=False,
            server_default="not_run",
        ),
        sa.Column("accepted_offer_revision_id", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("client_request_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("abandoned_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("booking_id"),
        sa.UniqueConstraint(
            "client_request_id",
            name="uq_bookings_client_request_id",
        ),
    )
    op.create_index(
        "idx_bookings_source_quote",
        "bookings",
        ["source_quote_id"],
    )
    op.create_index(
        "idx_bookings_status",
        "bookings",
        ["status"],
    )

    op.create_table(
        "booking_offer_revisions",
        sa.Column(
            "offer_revision_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("offer_revision_id"),
        sa.UniqueConstraint(
            "booking_id",
            "revision_number",
            name="uq_booking_offer_revision_number",
        ),
    )
    op.create_index(
        "idx_booking_offer_revisions_booking",
        "booking_offer_revisions",
        ["booking_id", "revision_number"],
    )

    op.create_table(
        "booking_passengers",
        sa.Column(
            "passenger_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("passenger_type", sa.Text(), nullable=False),
        sa.Column("quoted_age", sa.Integer(), nullable=True),
        sa.Column("given_name", sa.Text(), nullable=True),
        sa.Column("middle_name", sa.Text(), nullable=True),
        sa.Column("surname", sa.Text(), nullable=True),
        sa.Column("date_of_birth", sa.Text(), nullable=True),
        sa.Column("gender", sa.Text(), nullable=True),
        sa.Column("associated_adult_slot_index", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("passenger_id"),
        sa.UniqueConstraint(
            "booking_id",
            "slot_index",
            name="uq_booking_passenger_slot",
        ),
    )
    op.create_index(
        "idx_booking_passengers_booking",
        "booking_passengers",
        ["booking_id", "slot_index"],
    )

    op.create_table(
        "booking_contacts",
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone_country_code", sa.Text(), nullable=True),
        sa.Column("phone_number", sa.Text(), nullable=True),
        sa.Column("preferred_channel", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("booking_id"),
    )

    op.create_table(
        "booking_revalidations",
        sa.Column(
            "revalidation_id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("booking_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.Text(), nullable=False),
        sa.Column("source_offer_revision_id", sa.Integer(), nullable=True),
        sa.Column("candidate_offer_revision_id", sa.Integer(), nullable=True),
        sa.Column("provider_reference", sa.Text(), nullable=True),
        sa.Column("diff_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("stale_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("revalidation_id"),
    )
    op.create_index(
        "idx_booking_revalidations_booking",
        "booking_revalidations",
        ["booking_id", "revalidation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_booking_revalidations_booking",
        table_name="booking_revalidations",
    )
    op.drop_table("booking_revalidations")
    op.drop_table("booking_contacts")
    op.drop_index(
        "idx_booking_passengers_booking",
        table_name="booking_passengers",
    )
    op.drop_table("booking_passengers")
    op.drop_index(
        "idx_booking_offer_revisions_booking",
        table_name="booking_offer_revisions",
    )
    op.drop_table("booking_offer_revisions")
    op.drop_index("idx_bookings_status", table_name="bookings")
    op.drop_index("idx_bookings_source_quote", table_name="bookings")
    op.drop_table("bookings")
