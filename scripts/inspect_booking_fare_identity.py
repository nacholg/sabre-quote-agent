from __future__ import annotations

import argparse

from app.services.booking_repository import get_booking_repository


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the frozen fare identity persisted on a Booking. "
            "Read-only: no Sabre request is sent."
        )
    )
    parser.add_argument("booking_id")
    args = parser.parse_args()

    repository = get_booking_repository()
    booking = repository.get(args.booking_id)
    if booking is None:
        raise SystemExit(f"Booking inexistente: {args.booking_id}")

    revision = booking.accepted_offer_revision
    if revision is None:
        raise SystemExit("Booking sin oferta aceptada.")

    snapshot = revision.snapshot
    fare = snapshot.fare

    segment_booking_classes = [
        str(segment.booking_class or "").strip() or "-"
        for segment in snapshot.segments
    ]

    print("=== BOOKING FARE IDENTITY INSPECTOR ===")
    print(f"booking_id={booking.booking_id}")
    print(f"environment={booking.environment}")
    print(f"status={booking.status.value}")
    print(f"accepted_offer_revision={revision.revision}")
    print(f"brand_code={fare.brand_code or '-'}")
    print(f"brand_name={fare.brand_name or '-'}")
    print(f"pricing_modifier={fare.pricing_modifier or '-'}")
    print(
        "fare_basis_codes="
        + (",".join(fare.fare_basis_codes) if fare.fare_basis_codes else "-")
    )
    print(
        "segment_booking_classes="
        + (",".join(segment_booking_classes) if segment_booking_classes else "-")
    )
    print(f"branded_component_count={len(fare.branded_components)}")

    for index, component in enumerate(fare.branded_components, start=1):
        print(
            "branded_component="
            f"{index}:"
            f"{component.begin_airport or '-'}-"
            f"{component.end_airport or '-'};"
            f"fare_basis={component.fare_basis_code or '-'};"
            f"carrier={component.governing_carrier or '-'};"
            f"brand_code={component.brand_code or '-'};"
            f"brand_name={component.brand_name or '-'};"
            f"program_code={component.program_code or '-'}"
        )

    print("proof_status=REQUIRES_CERT_PROOF")
    print("read_only=true")
    print("no Sabre request was sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
