from __future__ import annotations

from datetime import datetime

from app.models.pnr_workspace import (
    PnrTicketing,
    PnrTicketingConstraint,
    PnrTicketingConstraintStatus,
)


def _deadline_is_interpretable(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False

    normalized = text
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


def interpret_pnr_ticketing_constraint(
    ticketing: PnrTicketing,
) -> PnrTicketingConstraint:
    """Interpret only structured ticketing evidence already in the snapshot.

    This service never parses Ticketing text or SSR free text to manufacture a
    deadline. Absence of a structured deadline never means that no deadline
    exists.
    """

    deadline = str(ticketing.deadline_at or "").strip() or None
    deadline_interpretable = _deadline_is_interpretable(deadline)

    common = dict(
        advisory_present=bool(ticketing.advisory_present),
        advisory_code=ticketing.advisory_code,
        advisory_status=ticketing.advisory_status,
        advisory_airline_code=ticketing.advisory_airline_code,
        deadline_at=deadline,
        deadline_interpretable=deadline_interpretable,
    )

    if deadline is not None and deadline_interpretable:
        return PnrTicketingConstraint(
            status=PnrTicketingConstraintStatus.STRUCTURED_DEADLINE,
            requires_deadline_lookup=False,
            message=(
                "Sabre devolvió un deadline estructurado interpretable. "
                "Esta iteración no evalúa todavía si está vencido."
            ),
            **common,
        )

    if deadline is not None:
        return PnrTicketingConstraint(
            status=PnrTicketingConstraintStatus.UNVERIFIED_DEADLINE,
            requires_deadline_lookup=True,
            message=(
                "Sabre devolvió un valor de deadline estructurado que no "
                "puede interpretarse con seguridad."
            ),
            **common,
        )

    if ticketing.advisory_present:
        return PnrTicketingConstraint(
            status=(
                PnrTicketingConstraintStatus.ADVISORY_WITHOUT_DEADLINE
            ),
            requires_deadline_lookup=True,
            message=(
                "Sabre devolvió un advisory de ticketing, pero no un "
                "deadline estructurado. Se requiere verificar el vencimiento."
            ),
            **common,
        )

    return PnrTicketingConstraint(
        status=PnrTicketingConstraintStatus.NO_STRUCTURED_CONSTRAINT,
        requires_deadline_lookup=True,
        message=(
            "No se observó una restricción estructurada de ticketing en "
            "esta lectura. Esto no demuestra que no exista un deadline."
        ),
        **common,
    )
