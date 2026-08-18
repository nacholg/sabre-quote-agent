import json
import zipfile
from pathlib import Path

from app.config import Settings
from app.models.quote_request import QuoteSearchRequest
from app.sabre.shopping import build_bfm_request
from app.support import create_support_bundle


def test_support_bundle_is_sanitized(tmp_path: Path):
    settings = Settings(
        sabre_env="PROD",
        sabre_base_url="https://api.platform.sabre.com",
        sabre_token_type="password",
        sabre_client_id="RY3A-Travellink",
        sabre_client_secret="SUPER_SECRET_VALUE",
        sabre_username="743052-RY3A-AA",
        sabre_password="PASSWORD_VALUE",
        sabre_pcc="RY3A",
    )
    search = QuoteSearchRequest(
        origin="EZE",
        destination="MIA",
        departure_date="2026-09-19",
    )
    payload = build_bfm_request(search, settings.sabre_pcc)
    exchange = {
        "method": "POST",
        "url": "https://api.platform.sabre.com/v5/offers/shop",
        "status_code": 200,
        "reason": "OK",
        "elapsed_ms": 123.4,
        "response_headers": {
            "content-type": "application/json",
            "set-cookie": "should-not-be-exported",
        },
        "response_json": {
            "groupedItineraryResponse": {
                "version": "7.2.2",
                "messages": [
                    {"code": "TRANSACTIONID", "text": "123456"},
                    {"code": "NAV", "text": "No Availability"},
                ],
                "statistics": {"itineraryCount": 0},
            }
        },
    }
    diagnostics = {
        "response_version": "7.2.2",
        "transaction_id": "123456",
        "itinerary_count": 0,
        "no_availability": True,
        "messages": exchange["response_json"]["groupedItineraryResponse"]["messages"],
    }

    bundle = create_support_bundle(
        settings=settings,
        search=search,
        payload=payload,
        exchange=exchange,
        diagnostics=diagnostics,
        root=tmp_path,
    )

    assert bundle.exists()
    with zipfile.ZipFile(bundle) as zf:
        names = set(zf.namelist())
        assert {"summary.json", "request.json", "response.json", "README.txt"} <= names
        text = "\n".join(zf.read(name).decode("utf-8") for name in names)
        assert "SUPER_SECRET_VALUE" not in text
        assert "PASSWORD_VALUE" not in text
        assert "\"Authorization\":" not in text
        assert "Bearer " not in text
        assert "Basic " not in text
        summary = json.loads(zf.read("summary.json"))
        assert summary["sabre"]["transaction_id"] == "123456"
        assert summary["http"]["status_code"] == 200
        assert summary["environment"] == "PROD"
