from pathlib import Path


SCRIPT = Path("scripts/store_pq_cert.py").read_text(encoding="utf-8").lower()


def test_store_pq_harness_requires_explicit_write_gate() -> None:
    assert "--confirm-cert-write" in SCRIPT
    assert "sabre_pnr_pricing_enabled" in SCRIPT
    assert "first write sólo permite 1 adt" in SCRIPT
    assert "no retry" in SCRIPT
    assert "manual verification required" in SCRIPT
