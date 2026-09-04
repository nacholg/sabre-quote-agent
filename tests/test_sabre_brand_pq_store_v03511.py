from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.sabre.soap_brand_pq_store import (
    SabreBrandPqStoreError,
    SabreSoapBrandPqStoreService,
    build_brand_price_command,
    normalize_name_number,
    parse_brand_price_screen,
)


SCREEN = """
19SEP DEPARTURE DATE-----LAST DAY TO PURCHASE 05SEP/2359
BASE FARE TAXES/FEES/CHARGES TOTAL
1- USD644.00 164.13XT USD808.13ADT
ADT-01 SLN7AHM5/L040
VALIDATING CARRIER - AA
2NDCHECKED BAG FEE-EZEMIA-USD100.00/AA/UP TO 50 POUNDS/23 KILOG
"""


def test_name_number_normalization() -> None:
    assert normalize_name_number("01.01") == "1.1"
    assert normalize_name_number("1.1") == "1.1"


def test_price_by_brand_command_is_not_fare_basis_forced() -> None:
    command = build_brand_price_command(
        currency="USD",
        brand_code="MAINFL",
        segment_numbers=[1],
        name_number="01.01",
        passenger_code="ADT",
    )

    assert command == "WPMUSD¥S1*BRMAINFL¥N1.1¥P1ADT"
    assert "*Q" not in command
    assert "SLN7AHM5" not in command
    assert "RQ" not in command


def test_retain_adds_rq_to_same_brand_command() -> None:
    command = build_brand_price_command(
        currency="USD",
        brand_code="MAINFL",
        segment_numbers=[1],
        name_number="1.1",
        passenger_code="ADT",
        retain=True,
    )

    assert command == "WPMUSD¥S1*BRMAINFL¥N1.1¥P1ADT¥RQ"


def test_multi_segment_command_keeps_brand_per_segment() -> None:
    command = build_brand_price_command(
        currency="USD",
        brand_code="MAINFL",
        segment_numbers=[1, 2],
        name_number="1.1",
        passenger_code="ADT",
    )

    assert command == (
        "WPMUSD¥S1*BRMAINFL¥S2*BRMAINFL¥N1.1¥P1ADT"
    )


def test_parse_real_cert_brand_price_screen() -> None:
    result = parse_brand_price_screen(
        screen=SCREEN,
        currency="USD",
        host_command="WPMUSD¥S1*BRMAINFL¥N1.1¥P1ADT",
    )

    assert result.total == Decimal("808.13")
    assert result.fare_basis == "SLN7AHM5/L040"
    assert result.validating_carrier == "AA"
    assert result.last_day_to_purchase_raw == "05SEP/2359"


def test_parser_uses_final_currency_amount_not_base_fare() -> None:
    result = parse_brand_price_screen(
        screen=SCREEN,
        currency="USD",
        host_command="WPMUSD",
    )
    assert result.total != Decimal("644.00")
    assert result.total == Decimal("808.13")


def test_missing_total_fails_closed() -> None:
    with pytest.raises(SabreBrandPqStoreError):
        parse_brand_price_screen(
            screen="NO FARE FOUND",
            currency="USD",
            host_command="WPMUSD",
        )


def test_store_refuses_without_fresh_docs_verification() -> None:
    service = SabreSoapBrandPqStoreService(
        SimpleNamespace(
            sabre_env="CERT",
            sabre_pnr_pricing_enabled=True,
        ),
        client=object(),
        session_service=object(),
    )

    with pytest.raises(
        SabreBrandPqStoreError,
        match="SECURE_FLIGHT_DOCS_REQUIRED",
    ):
        service.store(
            "OVFOTM",
            currency="USD",
            brand_code="MAINFL",
            segment_numbers=[1],
            name_number="01.01",
            passenger_code="ADT",
            expected_total=Decimal("808.13"),
            expected_segment_count=1,
            expected_validating_carrier="AA",
            secure_flight_docs_verified=False,
        )
