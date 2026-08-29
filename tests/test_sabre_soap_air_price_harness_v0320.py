from pathlib import Path


SCRIPT = Path("scripts/test_sabre_soap_air_price.py").read_text(
    encoding="utf-8"
).lower()


def test_air_price_harness_cannot_persist_pq() -> None:
    assert "retain=false" in SCRIPT
    assert "end_transaction=false" in SCRIPT
    assert "--confirm" not in SCRIPT
    assert "no pq was retained" in SCRIPT
