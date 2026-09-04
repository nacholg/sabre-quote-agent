from __future__ import annotations

from app.models.pnr_workspace import (
    PnrAutomaticSameBrandRefreshRequest,
    PnrAutomaticSameBrandRefreshResponse,
    PnrAutomaticSameBrandRefreshStatus,
    PnrPricingRefreshAttemptStatus,
)
from app.services.pnr_automatic_same_brand_refresh_service import (
    PnrAutomaticSameBrandRefreshService,
)
from app.services.pnr_pricing_refresh_attempt_service import (
    PnrPricingRefreshAttemptConflictError,
    PnrPricingRefreshAttemptIdempotencyError,
    PnrPricingRefreshAttemptService,
    get_pnr_pricing_refresh_attempt_service,
)


class PnrPricingRefreshExecutionService:
    """Persistent single-flight coordinator for the explicit pricing POST.

    The DB attempt is acquired before any provider work. The attempt becomes
    SUBMITTING immediately before the orchestrator calls the Sabre store.
    Once SUBMITTING is persisted, any unexpected exception is treated as
    reconciliation-required and the Booking remains locked against auto-retry.
    """

    def __init__(
        self,
        *,
        refresh_service: PnrAutomaticSameBrandRefreshService | None = None,
        attempt_service: PnrPricingRefreshAttemptService | None = None,
    ) -> None:
        self.refresh_service = (
            refresh_service or PnrAutomaticSameBrandRefreshService()
        )
        self.attempt_service = (
            attempt_service or get_pnr_pricing_refresh_attempt_service()
        )

    @staticmethod
    def _decode_result(
        result_json: str | None,
    ) -> PnrAutomaticSameBrandRefreshResponse | None:
        if not result_json:
            return None
        return PnrAutomaticSameBrandRefreshResponse.model_validate_json(
            result_json
        )

    @staticmethod
    def _busy_response(
        *,
        booking_id: str,
        reconciliation: bool,
        message: str,
    ) -> PnrAutomaticSameBrandRefreshResponse:
        return PnrAutomaticSameBrandRefreshResponse(
            booking_id=booking_id,
            status=(
                PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                if reconciliation
                else PnrAutomaticSameBrandRefreshStatus.BLOCKED
            ),
            sabre_mutation_performed=reconciliation,
            blockers=[
                (
                    "PRICING_REFRESH_RECONCILIATION_REQUIRED"
                    if reconciliation
                    else "PRICING_REFRESH_ALREADY_IN_PROGRESS"
                )
            ],
            message=message,
        )

    def _existing_result(
        self,
        *,
        booking_id: str,
        attempt,
    ) -> PnrAutomaticSameBrandRefreshResponse:
        replay = self._decode_result(attempt.result_json)
        if replay is not None and attempt.status in {
            PnrPricingRefreshAttemptStatus.NO_WRITE,
            PnrPricingRefreshAttemptStatus.SUCCEEDED,
            PnrPricingRefreshAttemptStatus.FAILED_SAFE,
            PnrPricingRefreshAttemptStatus.RECONCILIATION_REQUIRED,
        }:
            return replay

        if (
            attempt.status
            == PnrPricingRefreshAttemptStatus.RECONCILIATION_REQUIRED
        ):
            return self._busy_response(
                booking_id=booking_id,
                reconciliation=True,
                message=(
                    "Existe un refresh de pricing con resultado ambiguo. "
                    "No se permite reintentar automáticamente."
                ),
            )

        return self._busy_response(
            booking_id=booking_id,
            reconciliation=False,
            message=(
                "Ya existe un refresh de pricing en curso para este Booking. "
                "No se inicia un segundo write."
            ),
        )

    async def execute(
        self,
        booking_id: str,
        request: PnrAutomaticSameBrandRefreshRequest,
    ) -> PnrAutomaticSameBrandRefreshResponse:
        try:
            prepared = self.attempt_service.prepare(
                booking_id=booking_id,
                client_request_id=request.client_request_id,
                expected_brand_code=request.expected_brand_code,
                expected_currency=request.expected_currency,
                expected_total=request.expected_total,
            )
        except PnrPricingRefreshAttemptConflictError as exc:
            attempt = exc.attempt
            if attempt is not None:
                return self._existing_result(
                    booking_id=booking_id,
                    attempt=attempt,
                )
            return self._busy_response(
                booking_id=booking_id,
                reconciliation=False,
                message=str(exc),
            )

        attempt = prepared.attempt
        if not prepared.created:
            return self._existing_result(
                booking_id=booking_id,
                attempt=attempt,
            )

        attempt_id = attempt.pricing_refresh_attempt_id

        def before_store() -> None:
            self.attempt_service.mark_submitting(attempt_id)

        try:
            result = await self.refresh_service.refresh(
                booking_id,
                expected_brand_code=request.expected_brand_code,
                expected_currency=request.expected_currency,
                expected_total=request.expected_total,
                before_store=before_store,
            )
        except KeyError:
            current = self.attempt_service.get(attempt_id)
            if (
                current is not None
                and current.status
                == PnrPricingRefreshAttemptStatus.PREPARED
            ):
                self.attempt_service.mark_no_write(
                    attempt_id,
                    result_json=(
                        PnrAutomaticSameBrandRefreshResponse(
                            booking_id=booking_id,
                            status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                            blockers=["BOOKING_NOT_FOUND"],
                            message="Reserva no encontrada.",
                        ).model_dump_json()
                    ),
                    error_code="BOOKING_NOT_FOUND",
                    error_message="Reserva no encontrada.",
                )
            raise
        except Exception as exc:
            current = self.attempt_service.get(attempt_id)
            if (
                current is not None
                and current.status
                == PnrPricingRefreshAttemptStatus.SUBMITTING
            ):
                ambiguous = PnrAutomaticSameBrandRefreshResponse(
                    booking_id=booking_id,
                    status=(
                        PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                    ),
                    sabre_mutation_performed=True,
                    blockers=[
                        "PRICING_REFRESH_EXCEPTION_AFTER_SUBMITTING"
                    ],
                    message=(
                        "El refresh falló después de entrar en SUBMITTING. "
                        "No se reintenta automáticamente; se requiere "
                        "reconciliación."
                    ),
                )
                self.attempt_service.mark_reconciliation_required(
                    attempt_id,
                    result_json=ambiguous.model_dump_json(),
                    error_code="EXCEPTION_AFTER_SUBMITTING",
                    error_message=str(exc),
                )
                return ambiguous

            failed_prewrite = PnrAutomaticSameBrandRefreshResponse(
                booking_id=booking_id,
                status=PnrAutomaticSameBrandRefreshStatus.BLOCKED,
                sabre_mutation_performed=False,
                blockers=["PRICING_REFRESH_PREWRITE_FAILED"],
                message=str(exc),
            )
            if (
                current is not None
                and current.status
                == PnrPricingRefreshAttemptStatus.PREPARED
            ):
                self.attempt_service.mark_no_write(
                    attempt_id,
                    result_json=failed_prewrite.model_dump_json(),
                    error_code="PREWRITE_FAILED",
                    error_message=str(exc),
                )
            return failed_prewrite

        result_json = result.model_dump_json()
        current = self.attempt_service.get(attempt_id)
        if current is None:
            # We cannot prove the lifecycle row survived. If the provider may
            # have been entered, fail closed rather than encourage a retry.
            return PnrAutomaticSameBrandRefreshResponse(
                booking_id=booking_id,
                confirmation_id=result.confirmation_id,
                status=(
                    PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
                    if result.sabre_mutation_performed
                    else PnrAutomaticSameBrandRefreshStatus.BLOCKED
                ),
                sabre_mutation_performed=result.sabre_mutation_performed,
                blockers=["PRICING_REFRESH_ATTEMPT_STATE_LOST"],
                message=(
                    "No se pudo verificar el lifecycle persistido del refresh."
                ),
            )

        if current.status == PnrPricingRefreshAttemptStatus.PREPARED:
            self.attempt_service.mark_no_write(
                attempt_id,
                result_json=result_json,
                error_code=(
                    result.blockers[0] if result.blockers else None
                ),
                error_message=result.message,
            )
            return result

        if current.status != PnrPricingRefreshAttemptStatus.SUBMITTING:
            return self._existing_result(
                booking_id=booking_id,
                attempt=current,
            )

        if result.status == PnrAutomaticSameBrandRefreshStatus.UPDATED:
            self.attempt_service.mark_succeeded(
                attempt_id,
                result_json=result_json,
                pricing_authority_id=result.pricing_authority_id,
            )
            return result

        if result.status == PnrAutomaticSameBrandRefreshStatus.FAILED_SAFE:
            self.attempt_service.mark_failed_safe(
                attempt_id,
                result_json=result_json,
                error_code=(
                    result.blockers[0] if result.blockers else None
                ),
                error_message=result.message,
            )
            return result

        if (
            result.status
            == PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED
        ):
            self.attempt_service.mark_reconciliation_required(
                attempt_id,
                result_json=result_json,
                error_code=(
                    result.blockers[0]
                    if result.blockers
                    else "RECONCILIATION_REQUIRED"
                ),
                error_message=(
                    result.message
                    or "Sabre pricing outcome requires reconciliation."
                ),
            )
            return result

        # SUBMITTING means the store boundary was crossed. Any other outcome
        # here is unexpected and must retain the lock rather than release it.
        ambiguous = PnrAutomaticSameBrandRefreshResponse(
            booking_id=booking_id,
            confirmation_id=result.confirmation_id,
            status=PnrAutomaticSameBrandRefreshStatus.RECONCILIATION_REQUIRED,
            brand_code=result.brand_code,
            source_total=result.source_total,
            candidate_total=result.candidate_total,
            current_total=result.current_total,
            price_difference=result.price_difference,
            pricing_authority_id=result.pricing_authority_id,
            sabre_mutation_performed=True,
            blockers=["UNEXPECTED_RESULT_AFTER_SUBMITTING"],
            message=(
                "El provider boundary fue iniciado pero el resultado no "
                "corresponde a un estado terminal seguro."
            ),
        )
        self.attempt_service.mark_reconciliation_required(
            attempt_id,
            result_json=ambiguous.model_dump_json(),
            error_code="UNEXPECTED_RESULT_AFTER_SUBMITTING",
            error_message=ambiguous.message or "",
        )
        return ambiguous


def get_pnr_pricing_refresh_execution_service(
) -> PnrPricingRefreshExecutionService:
    return PnrPricingRefreshExecutionService()
