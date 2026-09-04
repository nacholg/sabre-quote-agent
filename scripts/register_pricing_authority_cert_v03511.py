from __future__ import annotations

import argparse
from decimal import Decimal

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.models.booking import BookingStatus, PnrAttemptStatus
from app.models.pnr_workspace import PnrPricingAuthority
from app.models.quote_request import PassengerKind
from app.sabre.soap_brand_pq_store import SabreSoapBrandPqStoreService
from app.sabre.soap_pnr_read import SabreSoapPnrReadService
from app.services.booking_pnr_attempt_service import BookingPnrAttemptService
from app.services.booking_repository import get_booking_repository
from app.services.pnr_pricing_authority_backfill_service import (
    PnrPricingAuthorityBackfillError,
    verify_pnr_pricing_authority_backfill,
)
from app.services.pnr_pricing_authority_repository import (
    PnrPricingAuthorityRepository,
)
from app.services.pnr_workspace_service import PnrWorkspaceService


def _load(booking_id: str):
    repository = get_booking_repository()
    booking = repository.get(booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {booking_id}")
    if booking.environment != "cert":
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: Booking no es CERT."
        )
    if booking.status != BookingStatus.PNR_CREATED:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: Booking no está PNR_CREATED."
        )

    attempt = BookingPnrAttemptService(
        booking_repository=repository
    ).get(booking.booking_id)
    if (
        attempt is None
        or attempt.status != PnrAttemptStatus.SUCCEEDED
        or not attempt.confirmation_id
    ):
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: no hay PNR SUCCEEDED."
        )

    revision = booking.accepted_offer_revision
    if revision is None:
        raise SystemExit("Booking sin oferta aceptada.")

    if len(revision.snapshot.segments) != 1:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: sólo 1 segmento en v0.35.11c2b2."
        )
    if len(revision.snapshot.passenger_mix) != 1:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: sólo 1 PTC en v0.35.11c2b2."
        )
    pax = revision.snapshot.passenger_mix[0]
    if pax.quantity != 1 or pax.type != PassengerKind.ADULT:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: sólo 1 ADT en v0.35.11c2b2."
        )

    return repository, booking, attempt, revision


def _same_authority(
    existing: PnrPricingAuthority,
    verified,
) -> bool:
    return (
        existing.booking_id == verified.booking_id
        and existing.confirmation_id == verified.confirmation_id
        and existing.price_quote_record_numbers
        == verified.price_quote_record_numbers
        and existing.brand_code == verified.brand_code
        and existing.original_total == verified.original_total
        and existing.current_total == verified.current_total
        and existing.currency == verified.currency
        and existing.validating_carrier == verified.validating_carrier
        and existing.fare_basis_codes == verified.fare_basis_codes
        and existing.purchase_deadline_raw
        == verified.purchase_deadline_raw
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Sabre reconciliation + explicit local DB backfill of "
            "one already-retained same-brand current PQ. Never sends RQ/EOT."
        )
    )
    parser.add_argument("booking_id")
    parser.add_argument("--brand", required=True)
    parser.add_argument("--expected-total", required=True)
    parser.add_argument("--expected-pq-record", required=True)
    parser.add_argument(
        "--confirm-local-write",
        action="store_true",
        help=(
            "Persist only the verified authority in the local DB. "
            "No Sabre mutation is performed."
        ),
    )
    args = parser.parse_args()

    repository, booking, attempt, revision = _load(args.booking_id)
    settings = get_settings("cert")

    if settings.sabre_env.strip().upper() != "CERT":
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: runtime no es CERT."
        )
    if settings.sabre_create_booking_enabled:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: CREATE_BOOKING debe estar False."
        )
    if settings.sabre_secure_flight_enabled:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: SECURE_FLIGHT debe estar False."
        )
    if settings.sabre_pnr_pricing_enabled:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: "
            "SABRE_PNR_PRICING_ENABLED debe estar False; este comando "
            "es deliberadamente read-only en Sabre."
        )
    if not settings.sabre_read_only:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: SABRE_READ_ONLY debe estar True."
        )

    fare = revision.snapshot.fare
    requested_brand = args.brand.strip().upper()
    accepted_brand = (fare.brand_code or "").strip().upper()
    if requested_brand != accepted_brand:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: --brand debe coincidir "
            "exactamente con el BrandID aceptado."
        )

    try:
        expected_total = Decimal(args.expected_total)
    except Exception as exc:
        raise SystemExit("--expected-total inválido.") from exc

    fresh = SabreSoapPnrReadService(settings).retrieve(
        attempt.confirmation_id
    )
    if len(fresh.snapshot.passengers) != 1:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: fresh TIR no tiene 1 pasajero."
        )
    name_number = fresh.snapshot.passengers[0].name_number
    if not name_number:
        raise SystemExit(
            "PRICING AUTHORITY BACKFILL REFUSAL: NameNumber no verificable."
        )

    preview = SabreSoapBrandPqStoreService(settings).preview(
        attempt.confirmation_id,
        currency=fare.currency.strip().upper(),
        brand_code=requested_brand,
        segment_numbers=[1],
        name_number=name_number,
        passenger_code="ADT",
        expected_segment_count=1,
    )

    try:
        verified = verify_pnr_pricing_authority_backfill(
            booking_id=booking.booking_id,
            confirmation_id=attempt.confirmation_id,
            fare=fare,
            snapshot=fresh.snapshot,
            requested_brand_code=requested_brand,
            expected_total=expected_total,
            expected_record_numbers=[args.expected_pq_record],
            preview=preview,
        )
    except PnrPricingAuthorityBackfillError as exc:
        print("RESULT=REFUSED")
        print(str(exc))
        return 2

    authority_repository = PnrPricingAuthorityRepository(
        booking_repository=repository
    )
    try:
        existing = authority_repository.latest(booking.booking_id)
    except OperationalError as exc:
        raise SystemExit(
            "La tabla de Pricing Authority no existe en la DB configurada. "
            "Ejecutá 'alembic upgrade head' y repetí."
        ) from exc

    print("=== VERIFIED PRICING AUTHORITY BACKFILL ===")
    print(f"booking_id={verified.booking_id}")
    print(f"confirmation_id={verified.confirmation_id}")
    print(
        "pq_records="
        f"{','.join(verified.price_quote_record_numbers)}"
    )
    print(f"brand={verified.brand_code}")
    print(f"brand_name={verified.brand_name or '-'}")
    print(
        f"original_total={verified.currency} {verified.original_total}"
    )
    print(
        f"current_total={verified.currency} {verified.current_total}"
    )
    print(
        f"price_difference={verified.current_total - verified.original_total}"
    )
    print(f"validating_carrier={verified.validating_carrier}")
    print(
        "fare_basis="
        f"{','.join(verified.fare_basis_codes)}"
    )
    print(
        "purchase_deadline_raw="
        f"{verified.purchase_deadline_raw}"
    )
    print(f"preview_host_command={preview.host_command}")
    print("sabre_mutation=false")
    print("retain=false")
    print("end_transaction=false")

    if existing is not None:
        if _same_authority(existing, verified):
            print()
            print(
                "RESULT=ALREADY_REGISTERED "
                f"pricing_authority_id={existing.pricing_authority_id}"
            )
            return 0
        print()
        print("RESULT=REFUSED")
        print(
            "Ya existe una Pricing Authority distinta para este Booking; "
            "este backfill histórico no la reemplaza ni agrega otra."
        )
        return 2

    if not args.confirm_local_write:
        print()
        print(
            "PREVIEW ONLY - Sabre fue read-only y no se escribió la DB."
        )
        print(
            "Para registrar la autoridad verificada, repetí con "
            "--confirm-local-write."
        )
        return 0

    authority = authority_repository.save(
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
        provider=verified.provider,
    )

    print()
    print("RESULT=REGISTERED")
    print(f"pricing_authority_id={authority.pricing_authority_id}")
    print(f"verified_at={authority.verified_at}")

    workspace = PnrWorkspaceService(
        booking_repository=repository
    ).get(booking.booking_id)

    print()
    print("=== FRESH WORKSPACE AFTER LOCAL BACKFILL ===")
    print(f"workspace={workspace.status.value}")
    print(
        "pricing_authority_current="
        f"{workspace.pricing_authority_current}"
    )
    print(
        "secure_flight_docs="
        f"{workspace.secure_flight_docs.status.value if workspace.secure_flight_docs else '-'}"
    )
    if workspace.ticket_candidate is not None:
        print(
            "ticket_candidate="
            f"{workspace.ticket_candidate.status.value}"
        )
        print(
            "candidate_total="
            f"{workspace.ticket_candidate.total_amount}"
        )
        print(
            "candidate_records="
            f"{workspace.ticket_candidate.price_quote_record_numbers}"
        )
        print(
            "candidate_blockers="
            f"{workspace.ticket_candidate.blockers}"
        )
    if workspace.pre_issue_readiness is not None:
        print(
            "pre_issue="
            f"{workspace.pre_issue_readiness.status.value}"
        )
        print(
            "pre_issue_blockers="
            f"{workspace.pre_issue_readiness.blockers}"
        )
    if workspace.final_pre_issue_gate is not None:
        print(
            "final_gate="
            f"{workspace.final_pre_issue_gate.status.value}"
        )
        print(
            "final_blockers="
            f"{workspace.final_pre_issue_gate.blockers}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
