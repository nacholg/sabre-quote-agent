from pathlib import Path


SCRIPT = Path("scripts/test_sabre_soap_pnr_read.py").read_text(
    encoding="utf-8"
).lower()


def test_soap_read_harness_is_read_only() -> None:
    assert "mode=read_only" in SCRIPT
    assert "sabre_create_booking_enabled" not in SCRIPT
    assert "--confirm" not in SCRIPT
    assert "ota_airprice" not in SCRIPT
