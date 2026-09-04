from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from app.db.database import Base
from app.models.pnr_workspace import PnrPricingRefreshAttemptStatus
from app.services.pnr_pricing_refresh_attempt_service import (
    PnrPricingRefreshAttemptConflictError,
    PnrPricingRefreshAttemptIdempotencyError,
    PnrPricingRefreshAttemptService,
)


def _service(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'attempts.db'}")
    Base.metadata.create_all(engine)
    repository = SimpleNamespace(engine=engine)
    return PnrPricingRefreshAttemptService(
        booking_repository=repository
    )


def _prepare(service, request_id="req-1"):
    return service.prepare(
        booking_id="B-1",
        client_request_id=request_id,
        expected_brand_code="mainfl",
        expected_currency="usd",
        expected_total=Decimal("808.13"),
        confirmation_id="ovfotm",
    )


def test_prepare_is_idempotent_for_exact_client_request(tmp_path) -> None:
    service = _service(tmp_path)

    first = _prepare(service)
    second = _prepare(service)

    assert first.created is True
    assert second.created is False
    assert (
        first.attempt.pricing_refresh_attempt_id
        == second.attempt.pricing_refresh_attempt_id
    )
    assert second.attempt.expected_brand_code == "MAINFL"
    assert second.attempt.expected_currency == "USD"


def test_same_client_request_cannot_change_price_identity(tmp_path) -> None:
    service = _service(tmp_path)
    _prepare(service)

    with pytest.raises(PnrPricingRefreshAttemptIdempotencyError):
        service.prepare(
            booking_id="B-1",
            client_request_id="req-1",
            expected_brand_code="MAINFL",
            expected_currency="USD",
            expected_total=Decimal("820.00"),
        )


def test_one_active_attempt_per_booking(tmp_path) -> None:
    service = _service(tmp_path)
    first = _prepare(service)

    with pytest.raises(PnrPricingRefreshAttemptConflictError) as exc:
        _prepare(service, "req-2")

    assert exc.value.attempt is not None
    assert (
        exc.value.attempt.pricing_refresh_attempt_id
        == first.attempt.pricing_refresh_attempt_id
    )


def test_no_write_releases_single_flight_guard(tmp_path) -> None:
    service = _service(tmp_path)
    first = _prepare(service)

    done = service.mark_no_write(
        first.attempt.pricing_refresh_attempt_id,
        result_json='{"status":"blocked"}',
    )
    assert done.status == PnrPricingRefreshAttemptStatus.NO_WRITE
    assert service.active_for_booking("B-1") is None

    second = _prepare(service, "req-2")
    assert second.created is True


def test_success_releases_guard_for_future_refresh(tmp_path) -> None:
    service = _service(tmp_path)
    first = _prepare(service)

    submitting = service.mark_submitting(
        first.attempt.pricing_refresh_attempt_id
    )
    assert submitting.status == PnrPricingRefreshAttemptStatus.SUBMITTING

    succeeded = service.mark_succeeded(
        first.attempt.pricing_refresh_attempt_id,
        result_json='{"status":"updated"}',
        pricing_authority_id=7,
    )
    assert succeeded.status == PnrPricingRefreshAttemptStatus.SUCCEEDED
    assert succeeded.pricing_authority_id == 7
    assert service.active_for_booking("B-1") is None

    assert _prepare(service, "req-2").created is True


def test_failed_safe_releases_guard(tmp_path) -> None:
    service = _service(tmp_path)
    first = _prepare(service)
    service.mark_submitting(first.attempt.pricing_refresh_attempt_id)

    failed = service.mark_failed_safe(
        first.attempt.pricing_refresh_attempt_id,
        result_json='{"status":"failed_safe"}',
        error_code="ROLLED_BACK",
        error_message="Ignore confirmed",
    )

    assert failed.status == PnrPricingRefreshAttemptStatus.FAILED_SAFE
    assert service.active_for_booking("B-1") is None
    assert _prepare(service, "req-2").created is True


def test_reconciliation_required_retains_guard_and_blocks_retry(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    first = _prepare(service)
    service.mark_submitting(first.attempt.pricing_refresh_attempt_id)

    reconc = service.mark_reconciliation_required(
        first.attempt.pricing_refresh_attempt_id,
        result_json='{"status":"reconciliation_required"}',
        error_code="AMBIGUOUS_EOT",
        error_message="Outcome unknown",
    )

    assert (
        reconc.status
        == PnrPricingRefreshAttemptStatus.RECONCILIATION_REQUIRED
    )
    active = service.active_for_booking("B-1")
    assert active is not None
    assert active.pricing_refresh_attempt_id == (
        first.attempt.pricing_refresh_attempt_id
    )

    with pytest.raises(PnrPricingRefreshAttemptConflictError):
        _prepare(service, "req-2")
