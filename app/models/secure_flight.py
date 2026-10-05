from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from app.models.pnr_workspace import PnrSecureFlightDocsCoverage

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)


class SecureFlightDocumentSummary(BaseModel):
    """Safe/redacted representation suitable for UI, audit metadata and logs."""

    document_type: Literal["P"]
    issuing_country: str
    nationality: str
    expiry_date: date
    document_number_present: bool


class SecureFlightDocumentInput(BaseModel):
    """Transient Secure Flight document data.

    The document number is deliberately excluded from model serialization and
    representation. Callers that need the clear value for the Sabre transport
    must opt in explicitly through ``document_number_value()``.

    This model is not a persistence model.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )

    document_type: Literal["P"] = "P"
    document_number: SecretStr = Field(
        repr=False,
        exclude=True,
    )
    issuing_country: str
    nationality: str
    expiry_date: date

    @field_validator("document_number")
    @classmethod
    def validate_document_number(cls, value: SecretStr) -> SecretStr:
        # Validate only after Pydantic has wrapped the input in SecretStr so
        # validation failures do not need to handle the raw clear-text value.
        raw = value.get_secret_value().strip()
        if not raw:
            raise ValueError("Document number es obligatorio.")
        return SecretStr(raw)

    @field_validator("issuing_country", "nationality")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 2 or not normalized.isalpha():
            raise ValueError("Debe ser un código ISO alpha-2.")
        return normalized

    def document_number_value(self) -> str:
        """Explicit clear-text access for the immediate Sabre transport only."""

        return self.document_number.get_secret_value()

    def redacted_summary(self) -> SecureFlightDocumentSummary:
        return SecureFlightDocumentSummary(
            document_type=self.document_type,
            issuing_country=self.issuing_country,
            nationality=self.nationality,
            expiry_date=self.expiry_date,
            document_number_present=bool(self.document_number_value()),
        )


class SecureFlightWriteReadinessStatus(StrEnum):
    READY = "ready"
    NOT_REQUIRED = "not_required"
    BLOCKED = "blocked"


class SecureFlightWriteReadiness(BaseModel):
    """Redacted, read-only decision for a future DOCS mutation."""

    status: SecureFlightWriteReadinessStatus
    booking_id: str
    confirmation_id: str | None = None
    target_name_number: str | None = None
    fresh_remote_read: bool = False
    document: SecureFlightDocumentSummary
    docs: PnrSecureFlightDocsCoverage | None = None
    blockers: list[str] = Field(default_factory=list)
