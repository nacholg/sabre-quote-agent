from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models.pnr_workspace import (
    PnrPricingSelection,
    PnrPricingSelectionStatus,
    PnrPurchaseDeadline,
    PnrPurchaseDeadlineStatus,
)


_TZ_NAME = "America/Argentina/Buenos_Aires"
_TZ = ZoneInfo(_TZ_NAME)
_PATTERN = re.compile(
    r"^LAST DAY TO PURCHASE\s+(\d{2})([A-Z]{3})(?:/(\d{4}))?$"
)
_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _stored_date(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _resolve_deadline(
    raw: str,
    *,
    stored_at: str | None,
) -> tuple[datetime | None, str | None]:
    normalized = " ".join(str(raw or "").strip().upper().split())
    match = _PATTERN.fullmatch(normalized)
    if match is None:
        return None, "PURCHASE_DEADLINE_FORMAT_UNSUPPORTED"

    day_text, month_text, hhmm = match.groups()
    if hhmm is None:
        return None, "PURCHASE_DEADLINE_TIME_MISSING"

    anchor = _stored_date(stored_at)
    if anchor is None:
        return None, "PURCHASE_DEADLINE_YEAR_UNRESOLVED"

    month = _MONTHS.get(month_text)
    if month is None:
        return None, "PURCHASE_DEADLINE_FORMAT_UNSUPPORTED"

    hour = int(hhmm[:2])
    minute = int(hhmm[2:])
    if hour > 23 or minute > 59:
        return None, "PURCHASE_DEADLINE_FORMAT_UNSUPPORTED"

    year = anchor.year
    if (month, int(day_text)) < (anchor.month, anchor.day):
        year += 1

    try:
        local_date = date(year, month, int(day_text))
    except ValueError:
        return None, "PURCHASE_DEADLINE_FORMAT_UNSUPPORTED"

    return datetime.combine(
        local_date,
        time(hour=hour, minute=minute),
        tzinfo=_TZ,
    ), None


def build_pnr_purchase_deadline(
    selection: PnrPricingSelection | None,
    *,
    now: datetime | None = None,
) -> PnrPurchaseDeadline:
    """Resolve purchase deadline only from ACTIVE PQ structured restrictions.

    The Sabre text must match the exact LAST DAY TO PURCHASE DDMMM/HHMM
    format. No ADTK free text or TAW arrangement is used here.

    Policy:
    - if the earliest PQ purchase deadline is <= 24h away, use it directly;
    - if it is > 24h away, cap the operational time limit at tomorrow 12:00
      America/Argentina/Buenos_Aires, but never later than the PQ deadline.
    """

    evaluated = now or datetime.now(_TZ)
    if evaluated.tzinfo is None:
        evaluated = evaluated.replace(tzinfo=_TZ)
    else:
        evaluated = evaluated.astimezone(_TZ)

    if (
        selection is None
        or selection.status != PnrPricingSelectionStatus.SELECTED
        or not selection.candidates
    ):
        return PnrPurchaseDeadline(
            status=PnrPurchaseDeadlineStatus.UNRESOLVED,
            blockers=["ACTIVE_PQ_UNAVAILABLE"],
            message=(
                "No hay un conjunto ACTIVE PQ utilizable para resolver "
                "el límite de compra."
            ),
        )

    source_records: list[str] = []
    raw_values: list[str] = []
    resolved: list[datetime] = []
    blockers: list[str] = []

    for quote in selection.candidates:
        record = str(quote.record_number or "").strip()
        if record:
            source_records.append(record)

        raw = str(quote.purchase_deadline_raw or "").strip()
        if not raw:
            blockers.append("PURCHASE_DEADLINE_MISSING")
            continue

        raw_values.append(raw)
        deadline, error = _resolve_deadline(
            raw,
            stored_at=quote.stored_at,
        )
        if error is not None:
            blockers.append(error)
            continue
        if deadline is not None:
            resolved.append(deadline)

    blockers = list(dict.fromkeys(blockers))
    source_records = list(dict.fromkeys(source_records))
    raw_values = list(dict.fromkeys(raw_values))

    if blockers or len(resolved) != len(selection.candidates):
        return PnrPurchaseDeadline(
            status=PnrPurchaseDeadlineStatus.UNRESOLVED,
            source_record_numbers=source_records,
            raw_values=raw_values,
            blockers=blockers or ["PURCHASE_DEADLINE_UNRESOLVED"],
            message=(
                "Uno o más PQ ACTIVE no tienen un LAST DAY TO PURCHASE "
                "con fecha y hora interpretable."
            ),
        )

    purchase_deadline = min(resolved)
    if purchase_deadline <= evaluated:
        return PnrPurchaseDeadline(
            status=PnrPurchaseDeadlineStatus.EXPIRED,
            purchase_deadline_at=purchase_deadline.isoformat(),
            operational_deadline_at=purchase_deadline.isoformat(),
            source_record_numbers=source_records,
            raw_values=raw_values,
            blockers=["PURCHASE_DEADLINE_EXPIRED"],
            message="El LAST DAY TO PURCHASE del PQ ACTIVE ya venció.",
        )

    remaining = purchase_deadline - evaluated
    tomorrow = evaluated.date() + timedelta(days=1)
    policy_cap = datetime.combine(
        tomorrow,
        time(hour=12),
        tzinfo=_TZ,
    )

    operational_deadline = purchase_deadline
    policy_capped = False
    if remaining > timedelta(hours=24):
        operational_deadline = min(
            purchase_deadline,
            policy_cap,
        )
        policy_capped = operational_deadline < purchase_deadline

    return PnrPurchaseDeadline(
        status=PnrPurchaseDeadlineStatus.RESOLVED,
        purchase_deadline_at=purchase_deadline.isoformat(),
        operational_deadline_at=operational_deadline.isoformat(),
        policy_cap_at=(
            policy_cap.isoformat()
            if remaining > timedelta(hours=24)
            else None
        ),
        policy_capped=policy_capped,
        source_record_numbers=source_records,
        raw_values=raw_values,
        blockers=[],
        message=(
            "Deadline operativo limitado a mañana 12:00 hora Buenos Aires."
            if policy_capped
            else "Deadline operativo igual al LAST DAY TO PURCHASE del PQ."
        ),
    )
