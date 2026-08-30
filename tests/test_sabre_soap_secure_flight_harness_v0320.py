from pathlib import Path


SCRIPT = Path("scripts/store_secure_flight_cert.py").read_text(
    encoding="utf-8"
)


def test_secure_flight_harness_is_cert_gated_and_minimum_only() -> None:
    assert "--confirm-cert-write" in SCRIPT
    assert "sabre_secure_flight_enabled" in SCRIPT
    assert "first write sólo permite 1 ADT" in SCRIPT
    assert "passport=not_requested" in SCRIPT
    assert "document_number=not_requested" in SCRIPT
    assert "NO RETRY" in SCRIPT
