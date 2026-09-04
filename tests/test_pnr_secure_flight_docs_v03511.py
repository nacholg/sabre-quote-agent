from app.models.pnr_workspace import (
    PnrPassenger,
    PnrSecureFlightDocsStatus,
    PnrSnapshot,
    PnrSpecialService,
)
from app.services.pnr_secure_flight_docs_service import (
    assess_pnr_secure_flight_docs,
)


def _snapshot(*, docs, name_number="01.01") -> PnrSnapshot:
    return PnrSnapshot(
        confirmation_id="OVFOTM",
        application_status="Complete",
        passengers=[
            PnrPassenger(
                name_number=name_number,
                passenger_type="ADT",
            )
        ],
        special_services=docs,
    )


def test_real_cert_docs_hk_association_is_complete() -> None:
    result = assess_pnr_secure_flight_docs(
        _snapshot(
            docs=[
                PnrSpecialService(
                    code="DOCS",
                    status="HK",
                    name_numbers=["01.01"],
                )
            ]
        )
    )

    assert result.status == PnrSecureFlightDocsStatus.COMPLETE
    assert result.covered_name_numbers == ["1.1"]
    assert result.missing_name_numbers == []
    assert result.unverified_name_numbers == []
    assert result.blockers == []


def test_missing_docs_fails_closed() -> None:
    result = assess_pnr_secure_flight_docs(_snapshot(docs=[]))

    assert result.status == PnrSecureFlightDocsStatus.MISSING
    assert result.missing_name_numbers == ["1.1"]
    assert "SECURE_FLIGHT_DOCS_MISSING" in result.blockers


def test_unassociated_docs_is_unverified_not_guessed() -> None:
    result = assess_pnr_secure_flight_docs(
        _snapshot(
            docs=[
                PnrSpecialService(
                    code="DOCS",
                    status="HK",
                    name_numbers=[],
                )
            ]
        )
    )

    assert result.status == PnrSecureFlightDocsStatus.UNVERIFIED
    assert result.unverified_name_numbers == ["1.1"]
    assert "SECURE_FLIGHT_DOCS_UNVERIFIED" in result.blockers


def test_explicit_non_hk_docs_is_missing_confirmed_docs() -> None:
    result = assess_pnr_secure_flight_docs(
        _snapshot(
            docs=[
                PnrSpecialService(
                    code="DOCS",
                    status="NN",
                    name_numbers=["1.1"],
                )
            ]
        )
    )

    assert result.status == PnrSecureFlightDocsStatus.MISSING
    assert result.missing_name_numbers == ["1.1"]


def test_missing_passenger_name_number_is_unverified() -> None:
    result = assess_pnr_secure_flight_docs(
        _snapshot(
            docs=[
                PnrSpecialService(
                    code="DOCS",
                    status="HK",
                    name_numbers=["01.01"],
                )
            ],
            name_number="",
        )
    )

    assert result.status == PnrSecureFlightDocsStatus.UNVERIFIED
    assert "unknown:1" in result.unverified_name_numbers
