from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.sabre.client import SabreClient
from app.sabre.errors import SabreAPIError
from app.services.reference_repository import ReferenceRepository, seed_reference_data


CANDIDATES = [
    {
        "name": "multi_airport_cities",
        "path": "/v1/lists/supported/cities",
        "status": "official-spec-discovered",
        "note": "Sabre still publishes multiairportcitylookupv1.yaml.",
    },
    {
        "name": "airports_at_city",
        "path": "/v1/lists/supported/cities/SAO/airports/",
        "status": "legacy-family-candidate",
        "note": "Candidate paired with the Multi-Airport City family; probe in CERT.",
    },
    {
        "name": "airlines",
        "path": "/v1/lists/utilities/airlines/",
        "status": "legacy-candidate",
        "note": "Historical Sabre REST Airline Lookup path; must be validated in CERT.",
    },
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Probe Sabre reference-data endpoints in CERT.")
    p.add_argument("--env", choices=["cert"], default="cert")
    p.add_argument("--sync", action="store_true", help="Persist recognizable results into data/reference.db.")
    p.add_argument("--db", default="data/reference.db")
    p.add_argument("--output", default="output/reference_probe.json")
    return p.parse_args()


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def first_value(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in d.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def sync_generic(repo: ReferenceRepository, name: str, payload: Any) -> dict[str, int]:
    counts = {"airports": 0, "cities": 0, "airlines": 0}
    seen = set()
    for d in walk_dicts(payload):
        code = first_value(d, ("code", "iataCode", "airportCode", "cityCode", "airlineCode", "carrierCode"))
        label = first_value(d, ("name", "airportName", "cityName", "airlineName", "carrierName"))
        country = first_value(d, ("countryCode", "country"))
        city_code = first_value(d, ("cityCode", "metropolitanAreaCode"))
        city_name = first_value(d, ("cityName",))
        if not isinstance(code, str):
            continue
        code = code.strip().upper()
        if not code or (name == "airlines" and len(code) not in {2, 3}) or (name != "airlines" and len(code) != 3):
            continue
        key=(name,code)
        if key in seen:
            continue
        seen.add(key)

        if name == "airlines":
            repo.upsert_airline(code=code, name=str(label) if label else None, source="sabre-probe")
            counts["airlines"] += 1
        elif name == "multi_airport_cities":
            repo.upsert_city(code=code, name=str(label) if label else None, country_code=str(country) if country else None, source="sabre-probe")
            counts["cities"] += 1
        elif name == "airports_at_city":
            repo.upsert_airport(
                code=code,
                name=str(label) if label else None,
                city_code=str(city_code or "SAO"),
                city_name=str(city_name) if city_name else None,
                country_code=str(country) if country else None,
                source="sabre-probe",
            )
            counts["airports"] += 1
    return counts


async def main() -> int:
    args=parse_args()
    settings=get_settings("cert")
    repo=ReferenceRepository(args.db)
    seed_reference_data(repo)

    results=[]
    async with SabreClient(settings) as client:
        for candidate in CANDIDATES:
            item={**candidate}
            try:
                payload=await client.get(candidate["path"])
                item["http_status"]=200
                item["reachable"]=True
                item["response_type"]=type(payload).__name__
                if isinstance(payload, dict):
                    item["top_level_keys"]=list(payload.keys())[:20]
                if args.sync:
                    item["synced"]=sync_generic(repo,candidate["name"],payload)
            except SabreAPIError as exc:
                item["http_status"]=exc.status_code
                item["reachable"]=False
                item["error"]=str(exc)
            except Exception as exc:
                item["reachable"]=False
                item["error"]=str(exc)
            results.append(item)

    report={
        "environment":"CERT",
        "pcc":settings.sabre_pcc,
        "base_url":settings.base_url,
        "results":results,
        "reference_db":str(Path(args.db)),
        "reference_stats":repo.stats(),
        "security":{
            "token_included":False,
            "password_included":False,
            "client_secret_included":False,
        },
    }
    out=Path(args.output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")

    print(f"Entorno: CERT")
    print(f"PCC: {settings.sabre_pcc}")
    for item in results:
        state=f"HTTP {item.get('http_status')}" if item.get("http_status") else "ERROR"
        print(f"{item['name']}: {state} - {'OK' if item.get('reachable') else 'NO'}")
        if item.get("synced"):
            print(f"  sync: {item['synced']}")
    print(f"Reference DB: {args.db}")
    print(f"Stats: {repo.stats()}")
    print(f"Reporte: {out}")
    return 0


if __name__=="__main__":
    raise SystemExit(asyncio.run(main()))
