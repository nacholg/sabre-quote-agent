from __future__ import annotations

import argparse
import json

from app.services.booking_create_pnr_builder import BookingCreatePnrPayloadBuilder


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Create Booking JSON for inspection only; sends nothing."
    )
    parser.add_argument("booking_id")
    args = parser.parse_args()

    payload, fingerprint = BookingCreatePnrPayloadBuilder().build_with_fingerprint(
        args.booking_id
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print()
    print(f"request_fingerprint={fingerprint}")
    print("DRY RUN ONLY - no request was sent to Sabre")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
