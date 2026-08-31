from __future__ import annotations

from typing import Callable, Literal

from app.config import (
    SabreEnvironmentMismatchError,
    Settings,
    get_settings,
)
from app.models.booking import (
    BookingCreatePnrRequest,
    BookingPnrAttemptRecord,
)
from app.sabre.create_booking import SabreCreateBookingProvider
from app.services.booking_create_pnr_builder import (
    BookingCreatePnrPayloadBuilder,
)
from app.services.booking_create_pnr_workflow_service import (
    BookingCreatePnrWorkflowService,
)
from app.services.booking_pnr_execution_service import (
    BookingPnrExecutionService,
)
from app.services.booking_repository import (
    BookingRepository,
    get_booking_repository,
)


class BookingCreatePnrUnavailableError(RuntimeError):
    """Create PNR is not enabled safely for the Booking/runtime."""


def _assert_create_pnr_runtime_enabled(
    settings: Settings,
    environment: Literal["cert", "prod"],
) -> None:
    actual = (
        "cert"
        if settings.sabre_env.strip().upper() == "CERT"
        else "prod"
    )

    if actual != environment:
        raise BookingCreatePnrUnavailableError(
            f"Booking pertenece a {environment.upper()} pero el runtime "
            f"Sabre está en {actual.upper()}."
        )

    if not settings.sabre_create_booking_enabled:
        raise BookingCreatePnrUnavailableError(
            "Create PNR está deshabilitado en este entorno."
        )

    if (
        environment == "prod"
        and not settings.sabre_create_booking_prod_enabled
    ):
        raise BookingCreatePnrUnavailableError(
            "Create PNR PROD requiere el segundo opt-in de producción."
        )

    create_path = settings.sabre_create_booking_path.rstrip("/") or "/"
    if (
        environment == "prod"
        and settings.sabre_read_only
        and create_path not in settings.allowed_paths
    ):
        raise BookingCreatePnrUnavailableError(
            "Create PNR está bloqueado por el guard read-only de PROD."
        )


class BookingCreatePnrService:
    """Canonical application entry point for the Create PNR button.

    Runtime write gates are checked before any revalidation or persisted
    PNR attempt. If enabled, the workflow performs a fresh exact
    revalidation and only then sends one branded/currency-locked
    Create Booking request.
    """

    def __init__(
        self,
        *,
        booking_repository: BookingRepository | None = None,
        settings_loader: (
            Callable[[Literal["prod", "cert"]], Settings] | None
        ) = None,
    ) -> None:
        self.booking_repository = (
            booking_repository or get_booking_repository()
        )
        self.settings_loader = settings_loader or get_settings

    async def execute(
        self,
        booking_id: str,
        request: BookingCreatePnrRequest,
    ) -> BookingPnrAttemptRecord:
        booking = self.booking_repository.get(booking_id)
        if booking is None:
            raise KeyError(booking_id)

        try:
            settings = self.settings_loader(booking.environment)
        except SabreEnvironmentMismatchError as exc:
            raise BookingCreatePnrUnavailableError(str(exc)) from exc

        _assert_create_pnr_runtime_enabled(
            settings,
            booking.environment,
        )

        provider = SabreCreateBookingProvider(settings=settings)

        # This is now the canonical proven path:
        # exact BrandID + exact selected currency + exact segments/classes.
        payload_builder = BookingCreatePnrPayloadBuilder(
            booking_repository=self.booking_repository,
            include_flight_pricing=True,
        )

        execution_service = BookingPnrExecutionService(
            booking_repository=self.booking_repository,
            payload_builder=payload_builder,
            provider=provider,
        )

        workflow = BookingCreatePnrWorkflowService(
            booking_repository=self.booking_repository,
            execution_service=execution_service,
        )

        return await workflow.execute(
            booking_id,
            request,
        )


def get_booking_create_pnr_service() -> BookingCreatePnrService:
    return BookingCreatePnrService()
