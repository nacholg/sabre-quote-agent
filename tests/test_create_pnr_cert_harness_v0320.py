from pathlib import Path


PREPARE = Path(
    "scripts/prepare_synthetic_cert_booking.py"
).read_text(encoding="utf-8")

EXECUTE = Path(
    "scripts/create_pnr_cert.py"
).read_text(encoding="utf-8")


def test_prepare_script_is_cert_only_and_never_creates_pnr():
    lower = PREPARE.lower()
    assert 'environment="cert"' in lower
    assert "source.environment != \"cert\"" in lower
    assert "bookingpnrexecutionservice" not in lower
    assert "sabrecreatebookingprovider" not in lower
    assert "create_booking(" not in lower


def test_prepare_script_uses_synthetic_pii_only():
    assert '"given_name": "CERTTEST"' in PREPARE
    assert '"surname": "BOOKING"' in PREPARE
    assert '"email": "test@example.com"' in PREPARE
    assert '"phone_number": "1100000000"' in PREPARE


def test_cert_write_harness_requires_explicit_confirmation_and_uuid():
    assert "--confirm-cert-write" in EXECUTE
    assert "--client-request-id" in EXECUTE
    assert "is mandatory with --confirm-cert-write" in EXECUTE


def test_cert_write_harness_has_double_environment_guard():
    lower = EXECUTE.lower()
    assert 'booking.environment != "cert"' in lower
    assert 'get_settings("cert")' in lower
    assert '!= "cert"' in lower
    assert "sabre_create_booking_enabled" in lower
    assert "sabre_create_booking_prod_enabled" in lower


def test_cert_write_harness_does_not_print_payload_or_pii():
    lower = EXECUTE.lower()
    assert "pii omitted from preview" in lower
    assert "print(payload" not in lower
    assert "json.dumps(payload" not in lower
    assert "givenname" not in lower
    assert "surname" not in lower
    assert "birthdate" not in lower
    assert "contactinfo" not in lower


def test_cert_write_harness_surfaces_reconciliation_without_retry():
    lower = EXECUTE.lower()
    assert "reconciliation_required" in lower
    assert "no retry" in lower
    assert "bookingpnrattemptservice" in lower

def test_cert_write_harness_prints_persisted_safe_diagnostic():
    lower = EXECUTE.lower()
    assert "error_message=" in lower
