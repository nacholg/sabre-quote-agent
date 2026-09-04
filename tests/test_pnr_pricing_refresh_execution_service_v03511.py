from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.pnr_workspace import (
    PnrAutomaticSameBrandRefreshRequest,
    PnrAutomaticSameBrandRefreshResponse,
    PnrAutomaticSameBrandRefreshStatus,
    PnrPricingRefreshAttemptStatus,
)
from app.services.pnr_pricing_refresh_attempt_service import (
    PnrPricingRefreshAttemptConflictError,
    PnrPricingRefreshAttemptPrepareResult,
)
from app.services.pnr_pricing_refresh_execution_service import (
    PnrPricingRefreshExecutionService,
)


def _request():
    return PnrAutomaticSameBrandRefreshRequest(
        confirm_same_brand_refresh=True,
        client_request_id="req-1",
        expected_brand_code="MAINFL",
        expected_currency="USD",
        expected_total=Decimal("808.13"),
    )


def _record(
    status=PnrPricingRefreshAttemptStatus.PREPARED,
    *,
    result_json=None,
):
    return SimpleNamespace(
        pricing_refresh_attempt_id=1,
        booking_id="B-1",
        client_request_id="req-1",
        expected_brand_code="MAINFL",
        expected_currency="USD",
        expected_total=Decimal("808.13"),
        status=status,
        result_json=result_json,
        pricing_authority_id=None,
    )


class Attempts:
    def __init__(self, *, created=True, existing=None, conflict=None):
        self.current = existing or _record()
        self.created = created
        self.conflict = conflict
        self.calls = []

    def prepare(self, **kwargs):
        self.calls.append(("prepare", kwargs))
        if self.conflict is not None:
            raise self.conflict
        return PnrPricingRefreshAttemptPrepareResult(
            attempt=self.current,
            created=self.created,
        )

    def get(self, attempt_id):
        return self.current

    def mark_submitting(self, attempt_id):
        self.calls.append(("submitting", attempt_id))
        self.current.status = PnrPricingRefreshAttemptStatus.SUBMITTING
        return self.current

    def mark_no_write(self, attempt_id, **kwargs):
        self.calls.append(("no_write", attempt_id, kwargs))
        self.current.status = PnrPricingRefreshAttemptStatus.NO_WRITE
        self.current.result_json = kwargs["result_json"]
        return self.current

    def mark_succeeded(self, attempt_id, **kwargs):
        self.calls.append(("succeeded", attempt_id, kwargs))
        self.current.status = PnrPricingRefreshAttemptStatus.SUCCEEDED
        self.current.result_json = kwargs["result_json"]
        return self.current

    def mark_failed_safe(self, attempt_id, **kwargs):
        self.calls.append(("failed_safe", attempt_id, kwargs))
        self.current.status = PnrPricingRefreshAttemptStatus.FAILED_SAFE
        self.current.result_json = kwargs["result_json"]
        return self.current

    def mark_reconciliation_required(self, attempt_id, **kwargs):
        self.calls.append(("reconciliation", attempt_id, kwargs))
        self.current.status = (
            PnrPricingRefreshAttemptStatus.RECONCILIATION_REQUIRED
        )
        self.current.result_json = kwargs["result_json"]
        return self.current


class Refresh:
    def __init__(self, result, *, raise_after_submit=None):
        self.result = result
        self.raise_after_submit = raise_after_submit
        self.calls = 0

    async def refresh(self, booking_id, **kwargs):
        self.calls += 1
        before_store = kwargs.pop("before_store")
        if self.result.status in {
            PnrAutomaticSameBrandRefreshStatus.UPDATED,
            PnrAutomaticSameBrandRefreshStatus.FAILED_SAFE,
            PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED,
        }:
            before_store()
            if self.raise_after_submit is not None:
                raise self.raise_after_submit
        return self.result


def _result(status, *, mutation=False):
    return PnrAutomaticSameBrandRefreshResponse(
        booking_id="B-1",
        confirmation_id="OVFOTM",
        status=status,
        brand_code="MAINFL",
        source_total=Decimal("781.33"),
        candidate_total=Decimal("808.13"),
        current_total=(
            Decimal("808.13")
            if status == PnrAutomaticSameBrandRefreshStatus.UPDATED
            else None
        ),
        price_difference=Decimal("26.80"),
        pricing_authority_id=(
            7 if status == PnrAutomaticSameBrandRefreshStatus.UPDATED else None
        ),
        sabre_mutation_performed=mutation,
        blockers=[],
    )


@pytest.mark.asyncio
async def test_updated_marks_submitting_then_succeeded() -> None:
    attempts = Attempts()
    refresh = Refresh(
        _result(
            PnrAutomaticSameBrandRefreshStatus.UPDATED,
            mutation=True,
        )
    )
    service = PnrPricingRefreshExecutionService(
        refresh_service=refresh,
        attempt_service=attempts,
    )

    result = await service.execute("B-1", _request())

    assert result.status == PnrAutomaticSameBrandRefreshStatus.UPDATED
    names = [call[0] for call in attempts.calls]
    assert names == ["prepare", "submitting", "succeeded"]


@pytest.mark.asyncio
async def test_prewrite_blocked_is_persisted_as_no_write() -> None:
    attempts = Attempts()
    refresh = Refresh(
        _result(PnrAutomaticSameBrandRefreshStatus.BLOCKED)
    )
    service = PnrPricingRefreshExecutionService(
        refresh_service=refresh,
        attempt_service=attempts,
    )

    result = await service.execute("B-1", _request())

    assert result.status == PnrAutomaticSameBrandRefreshStatus.BLOCKED
    names = [call[0] for call in attempts.calls]
    assert names == ["prepare", "no_write"]


@pytest.mark.asyncio
async def test_exact_terminal_retry_replays_without_provider_call() -> None:
    stored = _result(
        PnrAutomaticSameBrandRefreshStatus.UPDATED,
        mutation=True,
    )
    attempts = Attempts(
        created=False,
        existing=_record(
            PnrPricingRefreshAttemptStatus.SUCCEEDED,
            result_json=stored.model_dump_json(),
        ),
    )
    refresh = Refresh(
        _result(PnrAutomaticSameBrandRefreshStatus.BLOCKED)
    )
    service = PnrPricingRefreshExecutionService(
        refresh_service=refresh,
        attempt_service=attempts,
    )

    result = await service.execute("B-1", _request())

    assert result == stored
    assert refresh.calls == 0


@pytest.mark.asyncio
async def test_submitting_retry_never_calls_provider_again() -> None:
    attempts = Attempts(
        created=False,
        existing=_record(PnrPricingRefreshAttemptStatus.SUBMITTING),
    )
    refresh = Refresh(
        _result(PnrAutomaticSameBrandRefreshStatus.UPDATED, mutation=True)
    )
    service = PnrPricingRefreshExecutionService(
        refresh_service=refresh,
        attempt_service=attempts,
    )

    result = await service.execute("B-1", _request())

    assert result.status == PnrAutomaticSameBrandRefreshStatus.BLOCKED
    assert result.blockers == ["PRICING_REFRESH_ALREADY_IN_PROGRESS"]
    assert refresh.calls == 0


@pytest.mark.asyncio
async def test_reconciliation_retry_replays_without_provider_call() -> None:
    stored = _result(
        PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED,
        mutation=True,
    )
    attempts = Attempts(
        created=False,
        existing=_record(
            PnrPricingRefreshAttemptStatus.RECONCILIATION_REQUIRED,
            result_json=stored.model_dump_json(),
        ),
    )
    refresh = Refresh(
        _result(PnrAutomaticSameBrandRefreshStatus.UPDATED, mutation=True)
    )
    service = PnrPricingRefreshExecutionService(
        refresh_service=refresh,
        attempt_service=attempts,
    )

    result = await service.execute("B-1", _request())

    assert (
        result.status
        == PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
    )
    assert refresh.calls == 0


@pytest.mark.asyncio
async def test_unexpected_exception_after_submitting_locks_reconciliation() -> None:
    attempts = Attempts()
    refresh = Refresh(
        _result(PnrAutomaticSameBrandRefreshStatus.UPDATED, mutation=True),
        raise_after_submit=RuntimeError("transport died"),
    )
    service = PnrPricingRefreshExecutionService(
        refresh_service=refresh,
        attempt_service=attempts,
    )

    result = await service.execute("B-1", _request())

    assert (
        result.status
        == PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
    )
    assert result.blockers == [
        "PRICING_REFRESH_EXCEPTION_AFTER_SUBMITTING"
    ]
    assert attempts.current.status == (
        PnrPricingRefreshAttemptStatus.RECONCILIATION_REQUIRED
    )


@pytest.mark.asyncio
async def test_other_request_conflict_never_calls_provider() -> None:
    active = _record(PnrPricingRefreshAttemptStatus.SUBMITTING)
    attempts = Attempts(
        conflict=PnrPricingRefreshAttemptConflictError(
            "busy",
            attempt=active,
        )
    )
    refresh = Refresh(
        _result(PnrAutomaticSameBrandRefreshStatus.UPDATED, mutation=True)
    )
    service = PnrPricingRefreshExecutionService(
        refresh_service=refresh,
        attempt_service=attempts,
    )

    result = await service.execute("B-1", _request())

    assert result.status == PnrAutomaticSameBrandRefreshStatus.BLOCKED
    assert result.blockers == ["PRICING_REFRESH_ALREADY_IN_PROGRESS"]
    assert refresh.calls == 0
