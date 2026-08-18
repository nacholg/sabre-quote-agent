from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.reference_repository import ReferenceRepository, seed_reference_data


DEFAULT_AIRPORTS_URL = "https://ourairports.com/data/airports.csv"
DEFAULT_AIRLINES_URL = (
    "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Importa un catálogo global local de aeropuertos y aerolíneas."
    )
    p.add_argument("--db", default="data/reference.db")
    p.add_argument("--airports-file")
    p.add_argument("--airlines-file")
    p.add_argument("--airports-url", default=DEFAULT_AIRPORTS_URL)
    p.add_argument("--airlines-url", default=DEFAULT_AIRLINES_URL)
    p.add_argument("--no-download-airports", action="store_true")
    p.add_argument("--no-download-airlines", action="store_true")
    p.add_argument("--include-inactive-airlines", action="store_true")
    p.add_argument("--refresh", action="store_true", help="Reemplaza filas de estas fuentes antes de importar.")
    return p.parse_args()


def download_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "sabre-quote-agent-reference-import/0.17.3"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8-sig")


def read_text(file_path: str | None, url: str, *, allow_download: bool) -> str | None:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8-sig")
    if not allow_download:
        return None
    return download_text(url)


def import_ourairports(repo: ReferenceRepository, text: str) -> dict[str, int]:
    rows = list(csv.DictReader(io.StringIO(text)))
    usable: list[dict[str, str]] = []
    municipality_codes: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        code = (row.get("iata_code") or "").strip().upper()
        if len(code) != 3:
            continue
        usable.append(row)
        municipality = (row.get("municipality") or "").strip()
        if municipality:
            municipality_codes[municipality.casefold()].add(code)

    counts = {"airports": 0, "municipality_aliases": 0, "keyword_aliases": 0}
    for row in usable:
        code = (row.get("iata_code") or "").strip().upper()
        name = (row.get("name") or "").strip() or None
        municipality = (row.get("municipality") or "").strip() or None
        country = (row.get("iso_country") or "").strip().upper() or None
        ident = (row.get("ident") or "").strip().upper() or None
        try:
            lat = float(row["latitude_deg"]) if row.get("latitude_deg") else None
            lon = float(row["longitude_deg"]) if row.get("longitude_deg") else None
        except ValueError:
            lat = lon = None

        repo.upsert_airport(
            code=code,
            name=name,
            city_name=municipality,
            country_code=country,
            icao_code=ident if ident and len(ident) == 4 else None,
            latitude=lat,
            longitude=lon,
            source="ourairports",
        )
        counts["airports"] += 1

        # Municipality names are safe aliases only when they resolve to one IATA airport.
        if municipality and len(municipality_codes[municipality.casefold()]) == 1:
            repo.add_alias(municipality, "airport", code, source="ourairports")
            counts["municipality_aliases"] += 1

        keywords = (row.get("keywords") or "").strip()
        if keywords:
            for keyword in (item.strip() for item in keywords.split(",")):
                if len(keyword) >= 3:
                    repo.add_alias(keyword, "airport", code, source="ourairports")
                    counts["keyword_aliases"] += 1

    return counts


def import_openflights_airlines(
    repo: ReferenceRepository,
    text: str,
    *,
    include_inactive: bool = False,
) -> dict[str, int]:
    counts = {"airlines": 0, "aliases": 0, "skipped_inactive": 0}
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 8:
            continue
        _id, name, alias, iata, icao, _callsign, country, active = row[:8]
        iata = (iata or "").strip().upper()
        if iata in {"", "\\N"} or len(iata) not in {2, 3}:
            continue
        is_active = active.upper() == "Y"
        if not include_inactive and not is_active:
            counts["skipped_inactive"] += 1
            continue

        repo.upsert_airline(
            code=iata,
            name=None if name == "\\N" else name,
            icao_code=None if icao == "\\N" else icao,
            country=None if country == "\\N" else country,
            active=is_active,
            source="openflights",
        )
        counts["airlines"] += 1

        if alias not in {"", "\\N"}:
            repo.add_alias(alias, "airline", iata, source="openflights")
            counts["aliases"] += 1

    return counts


def main() -> int:
    args = parse_args()
    repo = ReferenceRepository(args.db)
    seed_reference_data(repo)

    if args.refresh:
        repo.delete_source("ourairports")
        repo.delete_source("openflights")

    print(f"Reference DB: {args.db}")

    try:
        airports_text = read_text(
            args.airports_file,
            args.airports_url,
            allow_download=not args.no_download_airports,
        )
        if airports_text:
            stats = import_ourairports(repo, airports_text)
            print(f"OurAirports: {stats}")
        else:
            print("OurAirports: omitido")
    except Exception as exc:
        print(f"OurAirports: ERROR - {exc}")

    try:
        airlines_text = read_text(
            args.airlines_file,
            args.airlines_url,
            allow_download=not args.no_download_airlines,
        )
        if airlines_text:
            stats = import_openflights_airlines(
                repo,
                airlines_text,
                include_inactive=args.include_inactive_airlines,
            )
            print(f"OpenFlights airlines: {stats}")
        else:
            print("OpenFlights airlines: omitido")
    except Exception as exc:
        print(f"OpenFlights airlines: ERROR - {exc}")

    print(f"Final stats: {repo.stats()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
