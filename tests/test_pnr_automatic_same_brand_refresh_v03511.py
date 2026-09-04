from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.booking import BookingStatus
from app.models.pnr_workspace import (
    PnrAutomaticSameBrandRefreshStatus,
    PnrPassenger,
    PnrPriceQuote,
    PnrSameBrandRequoteResponse,
    PnrSameBrandRequoteStatus,
    PnrSnapshot,
    PnrSpecialService,
)
from app.models.quote_request import PassengerKind
from app.sabre.soap_brand_pq_store import (
    SabreBrandPqStoreError,
    SabreBrandPqStoreReconciliationRequiredError,
    SabreBrandPqStoreResult,
    SabreBrandPriceResult,
)
from app.services.pnr_automatic_same_brand_refresh_service import (
    PnrAutomaticSameBrandRefreshService,
)


def _booking():
    fare = SimpleNamespace(
        brand_code="MAINFL",
        brand_name="MAIN CABIN FLEXIBLE",
        currency="USD",
        total_price=Decimal("781.33"),
        validating_carrier="AA",
    )
    revision = SimpleNamespace(
        snapshot=SimpleNamespace(
            fare=fare,
            segments=[object()],
            passenger_mix=[
                SimpleNamespace(
                    type=PassengerKind.ADULT,
                    quantity=1,
                )
            ],
        )
    )
    return SimpleNamespace(
        booking_id="B-1",
        environment="cert",
        status=BookingStatus.PNR_CREATED,
        accepted_offer_revision=revision,
    )


class Repo:
    def get(self, booking_id):
        return _booking() if booking_id == "B-1" else None


def _discovery(status=PnrSameBrandRequoteStatus.FOUND):
    return PnrSameBrandRequoteResponse(
        booking_id="B-1",
        confirmation_id="OVFOTM",
        status=status,
        read_only=True,
        source_brand_code="MAINFL",
        source_brand_name="MAIN CABIN FLEXIBLE",
        source_currency="USD",
        source_total=Decimal("781.33"),
        candidate_brand_code=(
            "MAINFL"
            if status == PnrSameBrandRequoteStatus.FOUND
            else None
        ),
        candidate_brand_name=(
            "MAIN CABIN FLEXIBLE"
            if status == PnrSameBrandRequoteStatus.FOUND
            else None
        ),
        candidate_currency=(
            "USD"
            if status == PnrSameBrandRequoteStatus.FOUND
            else None
        ),
        candidate_total=(
            Decimal("808.13")
            if status == PnrSameBrandRequoteStatus.FOUND
            else None
        ),
        price_difference=(
            Decimal("26.80")
            if status == PnrSameBrandRequoteStatus.FOUND
            else None
        ),
        candidate_fare_basis_codes=["SLN7AHM5/L040"],
    )


class Requote:
    def __init__(self, result=None):
        self.result = result or _discovery()
        self.calls = 0

    async def refresh(self, booking_id):
        self.calls += 1
        return self.result


def _snapshot(
    *,
    record,
    itinerary_changed,
    total,
    docs_status="HK",
):
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        passengers=[
            PnrPassenger(
                name_number="01.01",
                passenger_type="ADT",
            )
        ],
        price_quotes=[
            PnrPriceQuote(
                record_number=record,
                status="ACTIVE",
                validating_carrier="AA",
                passenger_type="ADT",
                passenger_quantity=1,
                passenger_name_numbers=["01.01"],
                total_amount=Decimal(total),
                total_currency="USD",
                fare_basis_codes=["SLN7AHM5/L040"],
                purchase_deadline_raw=(
                    "LAST DAY TO PURCHASE 03SEP/2359"
                    if itinerary_changed
                    else "LAST DAY TO PURCHASE 05SEP/2359"
                ),
                itinerary_changed=itinerary_changed,
            )
        ],
        special_services=[
            PnrSpecialService(
                code="DOCS",
                status=docs_status,
                name_numbers=["01.01"],
            )
        ],
    )


class Reader:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def retrieve(self, locator):
        value = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return SimpleNamespace(snapshot=value)


def _price(*, retain):
    command = "WPMUSD¥S1*BRMAINFL¥N1.1¥P1ADT"
    if retain:
        command += "¥RQ"
    return SabreBrandPriceResult(
        currency="USD",
        total=Decimal("808.13"),
        fare_basis="SLN7AHM5/L040",
        validating_carrier="AA",
        last_day_to_purchase_raw="05SEP/2359",
        host_command=command,
    )


def _stored():
    return SabreBrandPqStoreResult(
        preview=_price(retain=False),
        retained=_price(retain=True),
        end_transaction_status="Complete",
        flight_segment_count=1,
        session_close_ok=True,
    )


class Store:
    def __init__(self, result=None, error=None):
        self.result = result or _stored()
        self.error = error
        self.calls = 0

    def store(self, *args, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert kwargs["secure_flight_docs_verified"] is True
        assert kwargs["brand_code"] == "MAINFL"
        assert kwargs["expected_total"] == Decimal("808.13")
        return self.result


class AuthorityRepo:
    def __init__(self):
        self.saved = []

    def save(self, **kwargs):
        self.saved.append(kwargs)
        return SimpleNamespace(pricing_authority_id=len(self.saved))


def _settings(*, pricing=True):
    return SimpleNamespace(
        sabre_env="CERT",
        sabre_create_booking_enabled=False,
        sabre_secure_flight_enabled=False,
        sabre_pnr_pricing_enabled=pricing,
    )


def _service(
    *,
    requote=None,
    reader=None,
    store=None,
    authorities=None,
    settings=None,
):
    reader = reader or Reader(
        [
            _snapshot(
                record="1",
                itinerary_changed=True,
                total="781.33",
            ),
            _snapshot(
                record="2",
                itinerary_changed=False,
                total="808.13",
            ),
        ]
    )
    store = store or Store()
    authorities = authorities or AuthorityRepo()
    return (
        PnrAutomaticSameBrandRefreshService(
            booking_repository=Repo(),
            requote_service=requote or Requote(),
            pricing_authority_repository=authorities,
            settings_loader=lambda environment: (
                settings or _settings()
            ),
            reader_factory=lambda runtime: reader,
            store_factory=lambda runtime: store,
        ),
        reader,
        store,
        authorities,
    )


@pytest.mark.asyncio
async def test_success_persists_authority_only_after_new_current_pq() -> None:
    service, reader, store, authorities = _service()

    result = await service.refresh("B-1")

    assert result.status == PnrAutomaticSameBrandRefreshStatus.UPDATED
    assert result.sabre_mutation_performed is True
    assert result.source_total == Decimal("781.33")
    assert result.current_total == Decimal("808.13")
    assert result.price_difference == Decimal("26.80")
    assert result.pricing_authority_id == 1
    assert reader.calls == 2
    assert store.calls == 1
    assert len(authorities.saved) == 1
    assert authorities.saved[0]["price_quote_record_numbers"] == ["2"]
    assert authorities.saved[0]["provider"] == (
        "sabre_brand_pq_auto_refresh_v03511"
    )


@pytest.mark.asyncio
async def test_pricing_gate_disabled_never_calls_store() -> None:
    service, reader, store, authorities = _service(
        settings=_settings(pricing=False)
    )

    result = await service.refresh("B-1")

    assert result.status == PnrAutomaticSameBrandRefreshStatus.BLOCKED
    assert result.blockers == ["PNR_PRICING_GATE_DISABLED"]
    assert reader.calls == 0
    assert store.calls == 0
    assert authorities.saved == []


@pytest.mark.asyncio
async def test_missing_docs_never_calls_store() -> None:
    reader = Reader(
        [
            _snapshot(
                record="1",
                itinerary_changed=True,
                total="781.33",
                docs_status="NN",
            )
        ]
    )
    service, _, store, authorities = _service(reader=reader)

    result = await service.refresh("B-1")

    assert result.status == PnrAutomaticSameBrandRefreshStatus.BLOCKED
    assert result.blockers == ["SECURE_FLIGHT_DOCS_NOT_COMPLETE"]
    assert store.calls == 0
    assert authorities.saved == []


@pytest.mark.asyncio
async def test_existing_clean_current_pq_blocks_duplicate_store() -> None:
    reader = Reader(
        [
            _snapshot(
                record="2",
                itinerary_changed=False,
                total="808.13",
            )
        ]
    )
    service, _, store, authorities = _service(reader=reader)

    result = await service.refresh("B-1")

    assert result.status == PnrAutomaticSameBrandRefreshStatus.BLOCKED
    assert result.blockers == ["CURRENT_PQ_ALREADY_EXISTS"]
    assert store.calls == 0
    assert authorities.saved == []


@pytest.mark.asyncio
async def test_ambiguous_store_requires_reconciliation_and_no_authority() -> None:
    store = Store(
        error=SabreBrandPqStoreReconciliationRequiredError(
            "EndTransaction outcome ambiguous"
        )
    )
    service, reader, _, authorities = _service(store=store)

    result = await service.refresh("B-1")

    assert (
        result.status
        == PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
    )
    assert result.sabre_mutation_performed is True
    assert reader.calls == 1
    assert authorities.saved == []


@pytest.mark.asyncio
async def test_failed_safe_store_does_not_persist_authority() -> None:
    store = Store(error=SabreBrandPqStoreError("retain rolled back"))
    service, reader, _, authorities = _service(store=store)

    result = await service.refresh("B-1")

    assert result.status == PnrAutomaticSameBrandRefreshStatus.FAILED_SAFE
    assert authorities.saved == []
    assert reader.calls == 1


@pytest.mark.asyncio
async def test_post_store_same_record_requires_reconciliation() -> None:
    reader = Reader(
        [
            _snapshot(
                record="1",
                itinerary_changed=True,
                total="781.33",
            ),
            _snapshot(
                record="1",
                itinerary_changed=False,
                total="808.13",
            ),
        ]
    )
    service, _, store, authorities = _service(reader=reader)

    result = await service.refresh("B-1")

    assert (
        result.status
        == PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
    )
    assert result.blockers == [
        "POST_STORE_CURRENT_PQ_NOT_UNIQUE_NEW_RECORD"
    ]
    assert store.calls == 1
    assert authorities.saved == []


@pytest.mark.asyncio
async def test_not_required_performs_no_read_or_write() -> None:
    requote = Requote(
        PnrSameBrandRequoteResponse(
            booking_id="B-1",
            confirmation_id="OVFOTM",
            status=PnrSameBrandRequoteStatus.NOT_REQUIRED,
            read_only=True,
        )
    )
    service, reader, store, authorities = _service(requote=requote)

    result = await service.refresh("B-1")

    assert result.status == PnrAutomaticSameBrandRefreshStatus.NOT_REQUIRED
    assert result.sabre_mutation_performed is False
    assert reader.calls == 0
    assert store.calls == 0
    assert authorities.saved == []


class FailingSecondRead:
    def __init__(self):
        self.calls = 0

    def retrieve(self, locator):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                snapshot=_snapshot(
                    record="1",
                    itinerary_changed=True,
                    total="781.33",
                )
            )
        raise RuntimeError("provider down after EOT")


@pytest.mark.asyncio
async def test_post_eot_read_failure_requires_reconciliation_no_authority() -> None:
    reader = FailingSecondRead()
    service, _, store, authorities = _service(reader=reader)

    result = await service.refresh("B-1")

    assert (
        result.status
        == PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
    )
    assert result.blockers == ["POST_STORE_TIR_FAILED"]
    assert result.sabre_mutation_performed is True
    assert store.calls == 1
    assert authorities.saved == []
