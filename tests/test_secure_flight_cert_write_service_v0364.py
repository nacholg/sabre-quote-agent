from __future__ import annotations

from types import SimpleNamespace

from app.models.pnr_workspace import PnrSecureFlightDocsCoverage, PnrSecureFlightDocsStatus
from app.models.secure_flight import (
    SecureFlightCertWriteStatus,
    SecureFlightDocumentInput,
    SecureFlightWriteReadiness,
    SecureFlightWriteReadinessStatus,
)
from app.sabre.soap_secure_flight_docs import (
    SabreSoapSecureFlightDocsReconciliationRequiredError,
)
from app.services.secure_flight_cert_write_service import SecureFlightCertWriteService


BOOKING_ID = "B-TEST"
LOCATOR = "ABC123"
SECRET = "X1234567"


def _document():
    return SecureFlightDocumentInput(
        document_type="P",
        document_number=SECRET,
        issuing_country="AR",
        nationality="AR",
        expiry_date="2030-12-31",
    )


def _docs(status):
    return PnrSecureFlightDocsCoverage(
        status=status,
        passenger_count=1,
        covered_name_numbers=["1.1"] if status == PnrSecureFlightDocsStatus.COMPLETE else [],
        missing_name_numbers=["1.1"] if status == PnrSecureFlightDocsStatus.MISSING else [],
        unverified_name_numbers=[],
        blockers=[],
    )


def _readiness(status, *, blockers=None, docs=None, fresh=True):
    return SecureFlightWriteReadiness(
        status=status,
        booking_id=BOOKING_ID,
        confirmation_id=LOCATOR,
        target_name_number="1.1",
        fresh_remote_read=fresh,
        document=_document().redacted_summary(),
        docs=docs,
        blockers=blockers or [],
    )


class Readiness:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def assess(self, booking_id, *, document):
        self.calls += 1
        return self.results.pop(0)


class Repo:
    def get(self, booking_id):
        return SimpleNamespace(
            booking_id=BOOKING_ID,
            environment="cert",
            accepted_offer_revision=SimpleNamespace(
                snapshot=SimpleNamespace(segments=[object(), object()])
            ),
        )


class Passengers:
    def __init__(self):
        self.calls = 0

    def get(self, booking_id):
        self.calls += 1
        return SimpleNamespace(passengers=[SimpleNamespace(
            given_name="JUAN",
            middle_name=None,
            surname="LOPEZ",
            date_of_birth=SimpleNamespace(isoformat=lambda: "1985-04-15"),
            gender="M",
        )])


class Writer:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = []

    def store(self, locator, **kwargs):
        self.calls.append((locator, kwargs))
        if self.mode == "ambiguous":
            raise SabreSoapSecureFlightDocsReconciliationRequiredError("NO RETRY")
        if self.mode == "safe_failure":
            raise RuntimeError("pre-submit")
        return SimpleNamespace(
            application_status="Complete",
            session_close_ok=True,
        )


def _service(readiness, writer):
    passengers = Passengers()
    return SecureFlightCertWriteService(
        booking_repository=Repo(),
        passenger_service=passengers,
        readiness_service=readiness,
        settings_loader=lambda _: SimpleNamespace(sabre_env="CERT"),
        writer_factory=lambda _: writer,
    ), passengers


def test_not_required_never_calls_writer():
    readiness = Readiness(_readiness(
        SecureFlightWriteReadinessStatus.NOT_REQUIRED,
        docs=_docs(PnrSecureFlightDocsStatus.COMPLETE),
    ))
    writer = Writer()
    service, passengers = _service(readiness, writer)
    result = service.execute(BOOKING_ID, document=_document())
    assert result.status == SecureFlightCertWriteStatus.NOT_REQUIRED
    assert result.write_submitted is False
    assert writer.calls == []
    assert passengers.calls == 0


def test_blocked_never_calls_writer():
    readiness = Readiness(_readiness(
        SecureFlightWriteReadinessStatus.BLOCKED,
        blockers=["SECURE_FLIGHT_WRITE_DISABLED"],
        fresh=False,
    ))
    writer = Writer()
    service, passengers = _service(readiness, writer)
    result = service.execute(BOOKING_ID, document=_document())
    assert result.status == SecureFlightCertWriteStatus.BLOCKED
    assert writer.calls == []
    assert passengers.calls == 0


def test_ready_writes_once_then_requires_independent_not_required_proof():
    readiness = Readiness(
        _readiness(SecureFlightWriteReadinessStatus.READY, docs=_docs(PnrSecureFlightDocsStatus.MISSING)),
        _readiness(SecureFlightWriteReadinessStatus.NOT_REQUIRED, docs=_docs(PnrSecureFlightDocsStatus.COMPLETE)),
    )
    writer = Writer()
    service, _ = _service(readiness, writer)
    result = service.execute(BOOKING_ID, document=_document())
    assert result.status == SecureFlightCertWriteStatus.SUCCEEDED
    assert result.write_submitted is True
    assert readiness.calls == 2
    assert len(writer.calls) == 1
    _, kwargs = writer.calls[0]
    assert kwargs["document_number"] == SECRET
    assert kwargs["name_number"] == "1.1"


def test_ambiguous_write_requires_reconciliation_without_post_readiness():
    readiness = Readiness(_readiness(
        SecureFlightWriteReadinessStatus.READY,
        docs=_docs(PnrSecureFlightDocsStatus.MISSING),
    ))
    writer = Writer("ambiguous")
    service, _ = _service(readiness, writer)
    result = service.execute(BOOKING_ID, document=_document())
    assert result.status == SecureFlightCertWriteStatus.RECONCILIATION_REQUIRED
    assert result.write_submitted is True
    assert readiness.calls == 1
    assert len(writer.calls) == 1


def test_safe_pre_submit_failure_is_blocked():
    readiness = Readiness(_readiness(
        SecureFlightWriteReadinessStatus.READY,
        docs=_docs(PnrSecureFlightDocsStatus.MISSING),
    ))
    writer = Writer("safe_failure")
    service, _ = _service(readiness, writer)
    result = service.execute(BOOKING_ID, document=_document())
    assert result.status == SecureFlightCertWriteStatus.BLOCKED
    assert result.write_submitted is False
    assert result.blockers == ["SECURE_FLIGHT_WRITE_NOT_SUBMITTED"]


def test_post_write_missing_docs_requires_reconciliation():
    readiness = Readiness(
        _readiness(SecureFlightWriteReadinessStatus.READY, docs=_docs(PnrSecureFlightDocsStatus.MISSING)),
        _readiness(SecureFlightWriteReadinessStatus.READY, docs=_docs(PnrSecureFlightDocsStatus.MISSING)),
    )
    writer = Writer()
    service, _ = _service(readiness, writer)
    result = service.execute(BOOKING_ID, document=_document())
    assert result.status == SecureFlightCertWriteStatus.RECONCILIATION_REQUIRED
    assert result.write_submitted is True
    assert "SECURE_FLIGHT_POST_WRITE_NOT_VERIFIED" in result.blockers


def test_post_write_read_failure_requires_reconciliation():
    readiness = Readiness(
        _readiness(SecureFlightWriteReadinessStatus.READY, docs=_docs(PnrSecureFlightDocsStatus.MISSING)),
        _readiness(
            SecureFlightWriteReadinessStatus.BLOCKED,
            blockers=["SECURE_FLIGHT_FRESH_TIR_FAILED"],
            fresh=False,
        ),
    )
    writer = Writer()
    service, _ = _service(readiness, writer)
    result = service.execute(BOOKING_ID, document=_document())
    assert result.status == SecureFlightCertWriteStatus.RECONCILIATION_REQUIRED
    assert "SECURE_FLIGHT_FRESH_TIR_FAILED" in result.blockers


def test_result_serialization_never_exposes_document_number():
    readiness = Readiness(_readiness(
        SecureFlightWriteReadinessStatus.NOT_REQUIRED,
        docs=_docs(PnrSecureFlightDocsStatus.COMPLETE),
    ))
    service, _ = _service(readiness, Writer())
    result = service.execute(BOOKING_ID, document=_document())
    serialized = result.model_dump_json()
    assert SECRET not in serialized
    assert '"document_number":' not in serialized
    assert result.document.document_number_present is True
