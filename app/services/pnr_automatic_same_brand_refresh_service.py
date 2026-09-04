from __future__ import annotations

from decimal import Decimal

from app.config import get_settings
from app.models.booking import BookingStatus
from app.models.pnr_workspace import (
    PnrAutomaticSameBrandRefreshResponse,
    PnrAutomaticSameBrandRefreshStatus,
    PnrPricingSelectionStatus,
    PnrSameBrandRequoteStatus,
    PnrSecureFlightDocsStatus,
)
from app.models.quote_request import PassengerKind
from app.sabre.soap_brand_pq_store import (
    SabreBrandPqStoreError,
    SabreBrandPqStoreReconciliationRequiredError,
    SabreSoapBrandPqStoreService,
)
from app.sabre.soap_pnr_read import SabreSoapPnrReadService
from app.services.booking_repository import get_booking_repository
from app.services.pnr_pricing_authority_backfill_service import (
    PnrPricingAuthorityBackfillError,
    verify_pnr_pricing_authority_backfill,
)
from app.services.pnr_pricing_authority_repository import (
    PnrPricingAuthorityRepository,
)
from app.services.pnr_pricing_selection_service import select_pnr_pricing
from app.services.pnr_same_brand_requote_service import (
    PnrSameBrandRequoteService,
)
from app.services.pnr_secure_flight_docs_service import (
    assess_pnr_secure_flight_docs,
)


class PnrAutomaticSameBrandRefreshService:
    """CERT-only orchestration for one verified same-brand PQ refresh.

    Discovery remains read-only. A Sabre write is attempted only when the
    exact same-brand candidate is FOUND, fresh TIR confirms DOCS, no current
    clean PQ already exists, and the pricing gate is explicitly enabled.

    Pricing Authority is appended only after EndTransaction Complete and a
    mandatory fresh TIR reconciles the retained same-brand pricing.
    """

    def __init__(
        self,
        *,
        booking_repository=None,
        requote_service=None,
        pricing_authority_repository=None,
        settings_loader=None,
        reader_factory=None,
        store_factory=None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )
        self.requote_service = (
            requote_service
            or PnrSameBrandRequoteService(
                booking_repository=self.booking_repository
            )
        )
        self.pricing_authority_repository = (
            pricing_authority_repository
            or PnrPricingAuthorityRepository(
                booking_repository=self.booking_repository
            )
        )
        self.settings_loader = settings_loader or get_settings
        self.reader_factory = (
            reader_factory
            or (lambda settings: SabreSoapPnrReadService(settings))
        )
        self.store_factory = (
            store_factory
            or (lambda settings: SabreSoapBrandPqStoreService(settings))
        )

    @staticmethod
    def _response(
        *,
        booking_id: str,
        confirmation_id: str | None,
        status: PnrAutomaticSameBrandRefreshStatus,
        brand_code: str | None = None,
        source_total: Decimal | None = None,
        candidate_total: Decimal | None = None,
        current_total: Decimal | None = None,
        price_difference: Decimal | None = None,
        pricing_authority_id: int | None = None,
        sabre_mutation_performed: bool = False,
        blockers: list[str] | None = None,
        message: str | None = None,
    ) -> PnrAutomaticSameBrandRefreshResponse:
        return PnrAutomaticSameBrandRefreshResponse(
            booking_id=booking_id,
            confirmation_id=confirmation_id,
            status=status,
            brand_code=brand_code,
            source_total=source_total,
            candidate_total=candidate_total,
            current_total=current_total,
            price_difference=price_difference,
            pricing_authority_id=pricing_authority_id,
            sabre_mutation_performed=sabre_mutation_performed,
            blockers=blockers or [],
            message=message,
        )

    async def refresh(
        self,
        booking_id: str,
        *,
        expected_brand_code: str | None = None,
        expected_currency: str | None = None,
        expected_total: Decimal | None = None,
    ) -> PnrAutomaticSameBrandRefreshResponse:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)
        if booking.environment != "cert":
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                blockers=["BOOKING_NOT_CERT"],
                message="Automatic same-brand refresh sólo está habilitado en CERT.",
            )
        if booking.status != BookingStatus.PNR_CREATED:
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                blockers=["BOOKING_NOT_PNR_CREATED"],
                message="El Booking todavía no tiene un PNR operativo.",
            )

        revision = booking.accepted_offer_revision
        if revision is None:
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                blockers=["ACCEPTED_OFFER_MISSING"],
            )
        fare = revision.snapshot.fare
        brand_code = str(fare.brand_code or "").strip().upper() or None
        currency = str(fare.currency or "").strip().upper() or None
        original_total = fare.total_price
        carrier = (
            str(fare.validating_carrier or "").strip().upper() or None
        )
        if (
            brand_code is None
            or currency is None
            or original_total is None
            or carrier is None
        ):
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                blockers=["FROZEN_FARE_IDENTITY_INCOMPLETE"],
                message=(
                    "Brand, moneda, total y validating carrier deben estar "
                    "congelados antes de un refresh automático."
                ),
            )

        # Keep the proven v0.35.11 write surface deliberately narrow.
        if len(revision.snapshot.segments) != 1:
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                blockers=["UNSUPPORTED_SEGMENT_COUNT"],
            )
        if len(revision.snapshot.passenger_mix) != 1:
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                blockers=["UNSUPPORTED_PASSENGER_MIX"],
            )
        pax = revision.snapshot.passenger_mix[0]
        if pax.quantity != 1 or pax.type != PassengerKind.ADULT:
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                blockers=["UNSUPPORTED_PASSENGER_MIX"],
            )

        try:
            discovered = await self.requote_service.refresh(booking_id)
        except Exception as exc:
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                blockers=["REQUote_DISCOVERY_FAILED"],
                message=str(exc),
            )
        if discovered.status == PnrSameBrandRequoteStatus.NOT_REQUIRED:
            return self._response(
                booking_id=booking_id,
                confirmation_id=discovered.confirmation_id,
                status=PnrAutomaticSameBrandRefreshStatus.NOT_REQUIRED,
                brand_code=brand_code,
                source_total=original_total,
                message=discovered.message,
            )
        if discovered.status != PnrSameBrandRequoteStatus.FOUND:
            return self._response(
                booking_id=booking_id,
                confirmation_id=discovered.confirmation_id,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=discovered.candidate_total,
                price_difference=discovered.price_difference,
                blockers=(
                    list(discovered.blockers)
                    or [f"REQUote_{discovered.status.value.upper()}"]
                ),
                message=discovered.message,
            )

        if (
            str(discovered.candidate_brand_code or "").strip().upper()
            != brand_code
        ):
            return self._response(
                booking_id=booking_id,
                confirmation_id=discovered.confirmation_id,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=discovered.candidate_total,
                blockers=["DISCOVERY_BRAND_MISMATCH"],
            )
        candidate_total = discovered.candidate_total
        candidate_currency = (
            str(discovered.candidate_currency or "").strip().upper()
            or None
        )
        if candidate_total is None or candidate_currency != currency:
            return self._response(
                booking_id=booking_id,
                confirmation_id=discovered.confirmation_id,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                blockers=["DISCOVERY_PRICE_IDENTITY_INCOMPLETE"],
            )

        confirmed_values = (
            expected_brand_code,
            expected_currency,
            expected_total,
        )
        if any(value is not None for value in confirmed_values):
            if any(value is None for value in confirmed_values):
                return self._response(
                    booking_id=booking_id,
                    confirmation_id=discovered.confirmation_id,
                    status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                    brand_code=brand_code,
                    source_total=original_total,
                    candidate_total=candidate_total,
                    price_difference=discovered.price_difference,
                    blockers=["CONFIRMED_CANDIDATE_INCOMPLETE"],
                    message=(
                        "La acción de pricing debe confirmar BrandID, moneda "
                        "y total exactos."
                    ),
                )

            confirmed_brand = str(expected_brand_code).strip().upper()
            confirmed_currency = str(expected_currency).strip().upper()
            if (
                confirmed_brand != brand_code
                or confirmed_currency != candidate_currency
                or expected_total != candidate_total
            ):
                return self._response(
                    booking_id=booking_id,
                    confirmation_id=discovered.confirmation_id,
                    status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                    brand_code=brand_code,
                    source_total=original_total,
                    candidate_total=candidate_total,
                    price_difference=discovered.price_difference,
                    blockers=["CONFIRMED_CANDIDATE_CHANGED"],
                    message=(
                        "La tarifa same-brand cambió desde la confirmación "
                        "del agente. Revisá el nuevo importe antes de guardar."
                    ),
                )

        locator = str(discovered.confirmation_id or "").strip().upper()
        if not locator:
            return self._response(
                booking_id=booking_id,
                confirmation_id=None,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                blockers=["PNR_LOCATOR_MISSING"],
            )

        settings = self.settings_loader("cert")
        if str(settings.sabre_env or "").strip().upper() != "CERT":
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                blockers=["RUNTIME_NOT_CERT"],
            )
        if settings.sabre_create_booking_enabled:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                blockers=["CREATE_BOOKING_GATE_MUST_BE_FALSE"],
            )
        if settings.sabre_secure_flight_enabled:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                blockers=["SECURE_FLIGHT_GATE_MUST_BE_FALSE"],
            )
        if not settings.sabre_pnr_pricing_enabled:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                blockers=["PNR_PRICING_GATE_DISABLED"],
                message=(
                    "Same-brand candidate verificado por discovery, pero el "
                    "write de pricing permanece deshabilitado."
                ),
            )

        reader = self.reader_factory(settings)
        try:
            before = reader.retrieve(locator)
        except Exception as exc:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                blockers=["PREWRITE_TIR_FAILED"],
                message=str(exc),
            )
        docs = assess_pnr_secure_flight_docs(before.snapshot)
        if docs.status != PnrSecureFlightDocsStatus.COMPLETE:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                blockers=["SECURE_FLIGHT_DOCS_NOT_COMPLETE"],
                message=docs.message,
            )
        if len(before.snapshot.passengers) != 1:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                blockers=["PNR_PASSENGER_COUNT_UNSUPPORTED"],
            )
        name_number = before.snapshot.passengers[0].name_number
        if not name_number:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                blockers=["PNR_NAME_NUMBER_UNVERIFIED"],
            )

        before_selection = select_pnr_pricing(before.snapshot)
        before_records = list(before_selection.candidate_record_numbers)
        if (
            before_selection.status == PnrPricingSelectionStatus.SELECTED
            and any(
                quote.itinerary_changed is False
                for quote in before_selection.candidates
            )
        ):
            # Never stack another PQ over an already-current pricing record.
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                blockers=["CURRENT_PQ_ALREADY_EXISTS"],
            )

        store = self.store_factory(settings)
        try:
            stored = store.store(
                locator,
                currency=currency,
                brand_code=brand_code,
                segment_numbers=[1],
                name_number=name_number,
                passenger_code="ADT",
                expected_total=candidate_total,
                expected_segment_count=1,
                expected_validating_carrier=carrier,
                secure_flight_docs_verified=True,
            )
        except SabreBrandPqStoreReconciliationRequiredError as exc:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=(
                    PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                ),
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                sabre_mutation_performed=True,
                blockers=["SABRE_WRITE_AMBIGUOUS"],
                message=str(exc),
            )
        except SabreBrandPqStoreError as exc:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=PnrAutomaticSameBrandRefreshStatus.FAILED_SAFE,
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                blockers=["SABRE_WRITE_FAILED_SAFE"],
                message=str(exc),
            )

        if stored.end_transaction_status != "Complete":
            # Defensive only: the store service normally raises before this.
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=(
                    PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                ),
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                sabre_mutation_performed=True,
                blockers=["END_TRANSACTION_NOT_CONFIRMED"],
            )

        # Mandatory independent read-back after the commit. No authority is
        # persisted before this point. A read failure after confirmed EOT is
        # reconciliation-required and must never trigger another automatic PQ.
        try:
            after = reader.retrieve(locator)
        except Exception as exc:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=(
                    PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                ),
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                sabre_mutation_performed=True,
                blockers=["POST_STORE_TIR_FAILED"],
                message=str(exc),
            )
        after_selection = select_pnr_pricing(after.snapshot)
        after_records = list(after_selection.candidate_record_numbers)
        if (
            after_selection.status != PnrPricingSelectionStatus.SELECTED
            or len(after_selection.candidates) != 1
            or not after_records
            or after_records == before_records
            or any(record in before_records for record in after_records)
        ):
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=(
                    PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                ),
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                sabre_mutation_performed=True,
                blockers=["POST_STORE_CURRENT_PQ_NOT_UNIQUE_NEW_RECORD"],
                message=(
                    "EndTransaction completó, pero fresh TIR no identifica "
                    "inequívocamente un nuevo PQ current."
                ),
            )

        try:
            verified = verify_pnr_pricing_authority_backfill(
                booking_id=booking_id,
                confirmation_id=locator,
                fare=fare,
                snapshot=after.snapshot,
                requested_brand_code=brand_code,
                expected_total=candidate_total,
                expected_record_numbers=after_records,
                preview=stored.retained,
            )
        except PnrPricingAuthorityBackfillError as exc:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=(
                    PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                ),
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                price_difference=discovered.price_difference,
                sabre_mutation_performed=True,
                blockers=["POST_STORE_VERIFICATION_FAILED"],
                message=str(exc),
            )

        try:
            authority = self.pricing_authority_repository.save(
                booking_id=verified.booking_id,
                confirmation_id=verified.confirmation_id,
                price_quote_record_numbers=verified.price_quote_record_numbers,
                brand_code=verified.brand_code,
                brand_name=verified.brand_name,
                original_total=verified.original_total,
                current_total=verified.current_total,
                currency=verified.currency,
                validating_carrier=verified.validating_carrier,
                fare_basis_codes=verified.fare_basis_codes,
                purchase_deadline_raw=verified.purchase_deadline_raw,
                provider="sabre_brand_pq_auto_refresh_v03511",
            )
        except Exception as exc:
            return self._response(
                booking_id=booking_id,
                confirmation_id=locator,
                status=(
                    PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                ),
                brand_code=brand_code,
                source_total=original_total,
                candidate_total=candidate_total,
                current_total=verified.current_total,
                price_difference=(
                    verified.current_total - verified.original_total
                ),
                sabre_mutation_performed=True,
                blockers=["PRICING_AUTHORITY_PERSIST_FAILED"],
                message=str(exc),
            )

        return self._response(
            booking_id=booking_id,
            confirmation_id=locator,
            status=PnrAutomaticSameBrandRefreshStatus.UPDATED,
            brand_code=brand_code,
            source_total=original_total,
            candidate_total=candidate_total,
            current_total=verified.current_total,
            price_difference=(
                verified.current_total - verified.original_total
            ),
            pricing_authority_id=authority.pricing_authority_id,
            sabre_mutation_performed=True,
            message=(
                "Same-brand PQ retained, fresh TIR reconciled and Pricing "
                "Authority persisted."
            ),
        )

def get_pnr_automatic_same_brand_refresh_service(
) -> PnrAutomaticSameBrandRefreshService:
    return PnrAutomaticSameBrandRefreshService()
