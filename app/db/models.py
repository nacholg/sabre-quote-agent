from __future__ import annotations

from sqlalchemy import Index, Integer, Text
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
