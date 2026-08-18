from pathlib import Path
import subprocess
import sys

from app.models.api import QuoteSearchAPIRequest, QuoteSearchAPIResponse, RankedOption
from app.models.itinerary import BrandedComponent, FareOption, FlightSegment, ItineraryOption
from app.services.quote_repository import QuoteRepository
from decimal import Decimal


def _make_quote(db: Path) -> str:
    fare = FareOption(
        cabin="economy",
        currency="USD",
        price_per_passenger=Decimal("1000"),
        validating_carrier="AA",
        brand_name="MAIN CABIN",
        branded_components=[
            BrandedComponent(
                begin_airport="EZE",
                end_airport="MIA",
                fare_basis_code="QLN0AHM1",
                governing_carrier="AA",
                vendor_code="ATP",
            )
        ],
    )
    option = ItineraryOption(
        segments=[
            FlightSegment(
                marketing_carrier="AA",
                flight_number="908",
                departure_airport="EZE",
                arrival_airport="MIA",
                departure_at="2026-09-19T22:15:00-03:00",
                arrival_at="2026-09-20T06:20:00-04:00",
            )
        ],
        fare=fare,
        fare_options_by_currency={"USD": [fare]},
    )
    request = QuoteSearchAPIRequest(
        origin="EZE", destination="MIA", departure_date="2026-09-19"
    )
    response = QuoteSearchAPIResponse(
        environment="CERT",
        effective_currencies=["USD"],
        calls=[],
        result_count=1,
        options=[
            RankedOption(
                rank=1,
                score=Decimal("1"),
                stops=0,
                duration_minutes=545,
                ranking_currency="USD",
                ranking_price=Decimal("1000"),
                itinerary=option,
            )
        ],
        client_quote="TEST",
    )
    return QuoteRepository(db).create(request=request, response=response)


def test_contract_probe_requires_explicit_soap_contract(tmp_path):
    db = tmp_path/"quotes.db"
    quote_id = _make_quote(db)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/test_air_rules_contract.py",
            "--quote-id", quote_id,
            "--db", str(db),
            "--execute",
            "--output-dir", str(tmp_path/"out"),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 3
    assert "SOAP endpoint: FALTA" in result.stdout
    assert "Session Token: FALTA" in result.stdout
