from __future__ import annotations

from sqlalchemy import Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class QuoteRow(Base):
    __tablename__ = "quotes"

    quote_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    agent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    interpretation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_request_json: Mapped[str] = mapped_column(Text, nullable=False)
    quote_response_json: Mapped[str] = mapped_column(Text, nullable=False)
    selected_ranks_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default="[]",
    )
    client_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_quote_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    refreshed_quote_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_quotes_created_at", "created_at"),
    )


class QuoteFareSelectionRow(Base):
    __tablename__ = "quote_fare_selections"

    quote_id: Mapped[str] = mapped_column(Text, primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    fare_index: Mapped[int] = mapped_column(Integer, nullable=False)
    fare_json: Mapped[str] = mapped_column(Text, nullable=False)
    selected_at: Mapped[str] = mapped_column(Text, nullable=False)


class QuoteBookingDraftRow(Base):
    __tablename__ = "quote_booking_drafts"

    quote_id: Mapped[str] = mapped_column(Text, primary_key=True)
    passengers_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default="[]",
    )
    contact_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        server_default="{}",
    )
    received_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class BookingRow(Base):
    __tablename__ = "bookings"

    booking_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_quote_id: Mapped[str] = mapped_column(Text, nullable=False)
    selected_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    revalidation_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="not_run",
        server_default="not_run",
    )
    accepted_offer_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    client_request_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    abandoned_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "client_request_id",
            name="uq_bookings_client_request_id",
        ),
        Index("idx_bookings_source_quote", "source_quote_id"),
        Index("idx_bookings_status", "status"),
    )


class BookingOfferRevisionRow(Base):
    __tablename__ = "booking_offer_revisions"

    offer_revision_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    booking_id: Mapped[str] = mapped_column(Text, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "booking_id",
            "revision_number",
            name="uq_booking_offer_revision_number",
        ),
        Index(
            "idx_booking_offer_revisions_booking",
            "booking_id",
            "revision_number",
        ),
    )


class BookingPassengerRow(Base):
    __tablename__ = "booking_passengers"

    passenger_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    booking_id: Mapped[str] = mapped_column(Text, nullable=False)
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    passenger_type: Mapped[str] = mapped_column(Text, nullable=False)
    quoted_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    given_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    middle_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    surname: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_of_birth: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender: Mapped[str | None] = mapped_column(Text, nullable=True)
    associated_adult_slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "booking_id",
            "slot_index",
            name="uq_booking_passenger_slot",
        ),
        Index(
            "idx_booking_passengers_booking",
            "booking_id",
            "slot_index",
        ),
    )


class BookingContactRow(Base):
    __tablename__ = "booking_contacts"

    booking_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_country_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class BookingRevalidationRow(Base):
    __tablename__ = "booking_revalidations"

    revalidation_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    booking_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[str] = mapped_column(Text, nullable=False)
    source_offer_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_offer_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stale_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "idx_booking_revalidations_booking",
            "booking_id",
            "revalidation_id",
        ),
    )


class BookingPnrAttemptRow(Base):
    __tablename__ = "booking_pnr_attempts"

    pnr_attempt_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    booking_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_request_id: Mapped[str] = mapped_column(Text, nullable=False)
    booking_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_offer_revision_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    revalidation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    confirmation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "booking_id",
            name="uq_booking_pnr_attempt_booking",
        ),
        UniqueConstraint(
            "client_request_id",
            name="uq_booking_pnr_attempt_client_request",
        ),
        Index(
            "idx_booking_pnr_attempts_status",
            "status",
        ),
    )


class QuoteArtifactRow(Base):
    __tablename__ = "quote_artifacts"

    artifact_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    quote_id: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    selected_ranks_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default="[]",
    )
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index(
            "idx_quote_artifacts_quote_created",
            "quote_id",
            "artifact_id",
        ),
    )
