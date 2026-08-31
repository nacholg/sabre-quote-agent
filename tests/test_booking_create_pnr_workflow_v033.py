from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.booking import (
    BookingCreatePnrRequest,
    BookingStatus,
    RevalidationStatus,
)
from app.services.booking_create_pnr_workflow_service import (
    BookingCreatePnrFreshRevalidationError,
    BookingCreatePnrWorkflowService,
)
from app.services.booking_pnr_attempt_service import (
    BookingPnrAttemptIdempotencyConflictError,
)


class FakeRepository:
    def __init__(self, *, revision=4):
        self.booking = SimpleNamespace(revision=revision)

    def get(self, booking_id):
        return self.booking


class FakeAttemptService:
    def __init__(self, existing=None):
        self.existing = existing

    def get(self, booking_id):
        return self.existing


class FakeRevalidationService:
    def __init__(
        self,
        *,
        status=RevalidationStatus.MATCHED,
        booking_status=BookingStatus.READY_TO_CREATE_PNR,
        revision=5,
    ):
        self.status = status
        self.booking_status = booking_status
        self.revision = revision
        self.calls = []

    async def revalidate(self, booking_id, request):
        self.calls.append((booking_id, request.revision))
        return SimpleNamespace(
            revalidation_status=self.status,
            status=self.booking_status,
            booking_revision=self.revision,
        )


class FakeExecutionService:
    def __init__(self):
        self.calls = []

    async def execute(self, booking_id, request):
        self.calls.append(
            (
                booking_id,
                request.revision,
                str(request.client_request_id),
            )
        )
        return SimpleNamespace(status="succeeded")


@pytest.mark.asyncio
async def test_workflow_revalidates_fresh_then_uses_new_revision():
    request_id = uuid4()
    repository = FakeRepository(revision=4)
    revalidation = FakeRevalidationService(revision=5)
    execution = FakeExecutionService()

    workflow = BookingCreatePnrWorkflowService(
        booking_repository=repository,
        revalidation_service=revalidation,
        execution_service=execution,
        attempt_service=FakeAttemptService(),
    )

    await workflow.execute(
        "B-TEST",
        BookingCreatePnrRequest(
            revision=4,
            client_request_id=request_id,
        ),
    )

    assert revalidation.calls == [("B-TEST", 4)]
    assert execution.calls == [
        ("B-TEST", 5, str(request_id))
    ]


@pytest.mark.asyncio
async def test_workflow_stops_before_create_when_fresh_revalidation_changes():
    request_id = uuid4()
    revalidation = FakeRevalidationService(
        status=RevalidationStatus.PRICE_CHANGED,
        booking_status=BookingStatus.REQUIRES_AGENT_ACTION,
        revision=5,
    )
    execution = FakeExecutionService()

    workflow = BookingCreatePnrWorkflowService(
        booking_repository=FakeRepository(revision=4),
        revalidation_service=revalidation,
        execution_service=execution,
        attempt_service=FakeAttemptService(),
    )

    with pytest.raises(BookingCreatePnrFreshRevalidationError):
        await workflow.execute(
            "B-TEST",
            BookingCreatePnrRequest(
                revision=4,
                client_request_id=request_id,
            ),
        )

    assert revalidation.calls == [("B-TEST", 4)]
    assert execution.calls == []


@pytest.mark.asyncio
async def test_workflow_exact_retry_skips_new_revalidation():
    request_id = uuid4()

    existing = SimpleNamespace(
        client_request_id=str(request_id),
        booking_revision=5,
    )

    revalidation = FakeRevalidationService()
    execution = FakeExecutionService()

    workflow = BookingCreatePnrWorkflowService(
        booking_repository=FakeRepository(revision=6),
        revalidation_service=revalidation,
        execution_service=execution,
        attempt_service=FakeAttemptService(existing),
    )

    await workflow.execute(
        "B-TEST",
        BookingCreatePnrRequest(
            revision=4,
            client_request_id=request_id,
        ),
    )

    assert revalidation.calls == []
    assert execution.calls == [
        ("B-TEST", 5, str(request_id))
    ]


@pytest.mark.asyncio
async def test_workflow_different_key_never_creates_second_attempt():
    original_id = uuid4()
    second_id = uuid4()

    existing = SimpleNamespace(
        client_request_id=str(original_id),
        booking_revision=5,
    )

    revalidation = FakeRevalidationService()
    execution = FakeExecutionService()

    workflow = BookingCreatePnrWorkflowService(
        booking_repository=FakeRepository(revision=5),
        revalidation_service=revalidation,
        execution_service=execution,
        attempt_service=FakeAttemptService(existing),
    )

    with pytest.raises(BookingPnrAttemptIdempotencyConflictError):
        await workflow.execute(
            "B-TEST",
            BookingCreatePnrRequest(
                revision=5,
                client_request_id=second_id,
            ),
        )

    assert revalidation.calls == []
    assert execution.calls == []
