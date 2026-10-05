from __future__ import annotations

from types import SimpleNamespace

from app.models.booking import BookingStatus, PnrAttemptStatus
from app.models.pnr_workspace import (
    PnrPassenger,
    PnrSegment,
    PnrSnapshot,
    PnrSpecialService,
)
from app.models.quote_request import PassengerKind
from app.models.secure_flight import (
    SecureFlightDocumentInput,
    SecureFlightWriteReadinessStatus,
)
from app.services.secure_flight_write_readiness_service import (
    SecureFlightWriteReadinessService,
)


BOOKING_ID = "B-TEST"
LOCATOR = "ABC123"
SECRET = "X1234567"


def _document() -> SecureFlightDocumentInput:
    return SecureFlightDocumentInput(
        document_type="P",
        document_number=SECRET,
        issuing_country="AR",
        nationality="AR",
        expiry_date="2030-12-31",
    )


def _booking(*, status=BookingStatus.PNR_CREATED, environment="cert"):
    return SimpleNamespace(
        booking_id=BOOKING_ID,
        environment=environment,
        status=status,
        accepted_offer_revision=SimpleNamespace(
            snapshot=SimpleNamespace(segments=[object(), object()])
        ),
    )


def _attempt(*, status=PnrAttemptStatus.SUCCEEDED, locator=LOCATOR):
    return SimpleNamespace(status=status, confirmation_id=locator)


def _passengers():
    passenger = SimpleNamespace(
        passenger_type=PassengerKind.ADULT,
        given_name="JUAN",
        middle_name=None,
        surname="LOPEZ",
        date_of_birth=SimpleNamespace(isoformat=lambda: "1985-04-15"),
        gender="M",
    )
    return SimpleNamespace(passengers=[passenger])


def _snapshot(*, docs=False, docs_status="HK", name_number="01.01"):
    services = []
    if docs:
        services.append(
            PnrSpecialService(
                code="DOCS",
                status=docs_status,
                name_numbers=[name_number],
            )
        )
    return PnrSnapshot(
        confirmation_id=LOCATOR,
        application_status="Complete",
        passengers=[
            PnrPassenger(
                name_number=name_number,
                passenger_type="ADT",
                given_name="JUAN",
                surname="LOPEZ",
            )
        ],
        segments=[
            PnrSegment(segment_number="1"),
            PnrSegment(segment_number="2"),
        ],
        special_services=services,
    )


class Repo:
    def __init__(self, booking=None):
        self.booking = booking or _booking()

    def get(self, booking_id):
        assert booking_id == BOOKING_ID
        return self.booking


class Attempts:
    def __init__(self, attempt=None):
        self.attempt = attempt or _attempt()

    def get(self, booking_id):
        assert booking_id == BOOKING_ID
        return self.attempt


class Passengers:
    def __init__(self, response=None):
        self.response = response or _passengers()

    def get(self, booking_id):
        assert booking_id == BOOKING_ID
        return self.response


class Reader:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def retrieve(self, locator):
        self.calls.append(locator)
        return SimpleNamespace(
            confirmation_id=locator,
            snapshot=self.snapshot,
        )


def _settings(*, enabled=True, env="CERT"):
    return SimpleNamespace(
        sabre_env=env,
        sabre_secure_flight_enabled=enabled,
    )


def _service(
    *,
    booking=None,
    attempt=None,
    passengers=None,
    snapshot=None,
    settings=None,
):
    reader = Reader(snapshot or _snapshot())
    service = SecureFlightWriteReadinessService(
        booking_repository=Repo(booking),
        attempt_service=Attempts(attempt),
        passenger_service=Passengers(passengers),
        settings_loader=lambda _: settings or _settings(),
        reader_factory=lambda _: reader,
    )
    return service, reader


def test_ready_requires_fresh_tir_and_exact_single_passenger_binding() -> None:
    service, reader = _service()

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.READY
    assert result.confirmation_id == LOCATOR
    assert result.target_name_number == "1.1"
    assert result.fresh_remote_read is True
    assert result.docs is not None
    assert result.docs.missing_name_numbers == ["1.1"]
    assert result.blockers == []
    assert reader.calls == [LOCATOR]


def test_complete_docs_is_not_required_and_never_exposes_document_number() -> None:
    service, reader = _service(snapshot=_snapshot(docs=True))

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.NOT_REQUIRED
    assert result.fresh_remote_read is True
    assert reader.calls == [LOCATOR]
    serialized = result.model_dump_json()
    assert SECRET not in serialized
    assert '"document_number":' not in serialized
    assert result.document.document_number_present is True


def test_disabled_write_gate_blocks_before_remote_read() -> None:
    service, reader = _service(settings=_settings(enabled=False))

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.BLOCKED
    assert "SECURE_FLIGHT_WRITE_DISABLED" in result.blockers
    assert result.fresh_remote_read is False
    assert reader.calls == []


def test_non_cert_booking_blocks_before_remote_read() -> None:
    service, reader = _service(booking=_booking(environment="prod"))

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.BLOCKED
    assert "SECURE_FLIGHT_CERT_ONLY" in result.blockers
    assert reader.calls == []


def test_pnr_must_be_created_and_attempt_succeeded() -> None:
    service, reader = _service(
        booking=_booking(status=BookingStatus.READY_TO_CREATE_PNR),
        attempt=_attempt(status=PnrAttemptStatus.SUBMITTING, locator=None),
    )

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.BLOCKED
    assert "SECURE_FLIGHT_PNR_NOT_CREATED" in result.blockers
    assert "SECURE_FLIGHT_PNR_ATTEMPT_NOT_SUCCEEDED" in result.blockers
    assert reader.calls == []


def test_multi_passenger_is_explicitly_blocked_in_v0363() -> None:
    response = _passengers()
    response.passengers.append(
        SimpleNamespace(
            passenger_type=PassengerKind.ADULT,
            given_name="MARIA",
            middle_name=None,
            surname="MAURI",
            date_of_birth=SimpleNamespace(isoformat=lambda: "1986-05-01"),
            gender="F",
        )
    )
    service, reader = _service(passengers=response)

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.BLOCKED
    assert "SECURE_FLIGHT_SINGLE_ADT_ONLY" in result.blockers
    assert reader.calls == []


def test_name_binding_mismatch_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot.passengers[0].surname = "OTHER"
    service, reader = _service(snapshot=snapshot)

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.BLOCKED
    assert "SECURE_FLIGHT_PASSENGER_BINDING_MISMATCH" in result.blockers
    assert result.fresh_remote_read is True
    assert reader.calls == [LOCATOR]


def test_unassociated_docs_fails_closed_instead_of_guessing() -> None:
    snapshot = _snapshot()
    snapshot.special_services = [
        PnrSpecialService(code="DOCS", status="HK", name_numbers=[])
    ]
    service, reader = _service(snapshot=snapshot)

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.BLOCKED
    assert "SECURE_FLIGHT_DOCS_NOT_UNEQUIVOCALLY_MISSING" in result.blockers
    assert reader.calls == [LOCATOR]


def test_segment_count_mismatch_fails_closed() -> None:
    snapshot = _snapshot()
    snapshot.segments.pop()
    service, reader = _service(snapshot=snapshot)

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.BLOCKED
    assert "SECURE_FLIGHT_SEGMENT_COUNT_MISMATCH" in result.blockers
    assert reader.calls == [LOCATOR]


def test_fresh_tir_failure_is_blocked_without_leaking_secret() -> None:
    class FailingReader:
        def retrieve(self, locator):
            raise RuntimeError("read failed")

    service = SecureFlightWriteReadinessService(
        booking_repository=Repo(),
        attempt_service=Attempts(),
        passenger_service=Passengers(),
        settings_loader=lambda _: _settings(),
        reader_factory=lambda _: FailingReader(),
    )

    result = service.assess(BOOKING_ID, document=_document())

    assert result.status == SecureFlightWriteReadinessStatus.BLOCKED
    assert result.blockers == ["SECURE_FLIGHT_FRESH_TIR_FAILED"]
    assert SECRET not in result.model_dump_json()
