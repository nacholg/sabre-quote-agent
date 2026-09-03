from app.models.pnr_workspace import (
    PnrTicketing,
    PnrTicketingConstraintStatus,
)
from app.services.pnr_ticketing_constraint_service import (
    interpret_pnr_ticketing_constraint,
)


def test_adtk_without_structured_deadline_requires_lookup() -> None:
    result = interpret_pnr_ticketing_constraint(
        PnrTicketing(
            advisory_present=True,
            advisory_airline_code="1S",
            advisory_code="ADTK",
            advisory_status="KK",
        )
    )

    assert (
        result.status
        == PnrTicketingConstraintStatus.ADVISORY_WITHOUT_DEADLINE
    )
    assert result.advisory_present is True
    assert result.advisory_airline_code == "1S"
    assert result.advisory_code == "ADTK"
    assert result.advisory_status == "KK"
    assert result.deadline_at is None
    assert result.deadline_interpretable is False
    assert result.requires_deadline_lookup is True


def test_no_structured_constraint_never_means_no_deadline() -> None:
    result = interpret_pnr_ticketing_constraint(PnrTicketing())

    assert (
        result.status
        == PnrTicketingConstraintStatus.NO_STRUCTURED_CONSTRAINT
    )
    assert result.deadline_at is None
    assert result.requires_deadline_lookup is True
    assert "no demuestra" in (result.message or "").lower()


def test_structured_iso_deadline_is_preserved_not_expiry_evaluated() -> None:
    result = interpret_pnr_ticketing_constraint(
        PnrTicketing(
            advisory_present=True,
            advisory_code="ADTK",
            deadline_at="2026-09-10T18:00:00+00:00",
        )
    )

    assert (
        result.status
        == PnrTicketingConstraintStatus.STRUCTURED_DEADLINE
    )
    assert result.deadline_at == "2026-09-10T18:00:00+00:00"
    assert result.deadline_interpretable is True
    assert result.requires_deadline_lookup is False
    assert "no evalúa todavía" in (result.message or "")


def test_zulu_structured_deadline_is_interpretable() -> None:
    result = interpret_pnr_ticketing_constraint(
        PnrTicketing(deadline_at="2026-09-10T18:00:00Z")
    )

    assert (
        result.status
        == PnrTicketingConstraintStatus.STRUCTURED_DEADLINE
    )
    assert result.deadline_interpretable is True


def test_unparseable_structured_deadline_requires_lookup() -> None:
    result = interpret_pnr_ticketing_constraint(
        PnrTicketing(deadline_at="10SEP AT SOME TIME")
    )

    assert (
        result.status
        == PnrTicketingConstraintStatus.UNVERIFIED_DEADLINE
    )
    assert result.deadline_interpretable is False
    assert result.requires_deadline_lookup is True


def test_free_text_is_not_parsed_into_a_deadline() -> None:
    result = interpret_pnr_ticketing_constraint(
        PnrTicketing(
            ticketing_text="TKT BY 03SEP 1800",
            deadline_at=None,
        )
    )

    assert (
        result.status
        == PnrTicketingConstraintStatus.NO_STRUCTURED_CONSTRAINT
    )
    assert result.deadline_at is None
    assert result.requires_deadline_lookup is True
