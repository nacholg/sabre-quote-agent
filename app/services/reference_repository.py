from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path


def fold(value: str) -> str:
    value = unicodedata.normalize("NFD", value.lower().strip())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


class ReferenceRepository:
    def __init__(self, db_path: str | Path = "data/reference.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS airports (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    city_code TEXT,
                    city_name TEXT,
                    country_code TEXT,
                    icao_code TEXT,
                    latitude REAL,
                    longitude REAL,
                    source TEXT NOT NULL DEFAULT 'seed'
                );

                CREATE TABLE IF NOT EXISTS cities (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    country_code TEXT,
                    source TEXT NOT NULL DEFAULT 'seed'
                );

                CREATE TABLE IF NOT EXISTS airlines (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    icao_code TEXT,
                    country TEXT,
                    active INTEGER,
                    source TEXT NOT NULL DEFAULT 'seed'
                );

                CREATE TABLE IF NOT EXISTS aliases (
                    alias_folded TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    code TEXT NOT NULL,
                    alias_original TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'seed',
                    PRIMARY KEY (alias_folded, entity_type, code)
                );
                """
            )
            airport_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(airports)").fetchall()
            }
            for column, definition in {
                "icao_code": "TEXT",
                "latitude": "REAL",
                "longitude": "REAL",
            }.items():
                if column not in airport_columns:
                    conn.execute(f"ALTER TABLE airports ADD COLUMN {column} {definition}")

            airline_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(airlines)").fetchall()
            }
            for column, definition in {
                "icao_code": "TEXT",
                "country": "TEXT",
                "active": "INTEGER",
            }.items():
                if column not in airline_columns:
                    conn.execute(f"ALTER TABLE airlines ADD COLUMN {column} {definition}")

    def upsert_airport(
        self,
        *,
        code: str,
        name: str | None = None,
        city_code: str | None = None,
        city_name: str | None = None,
        country_code: str | None = None,
        icao_code: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        source: str = "sabre",
    ) -> None:
        code = code.upper()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO airports(
                    code,name,city_code,city_name,country_code,icao_code,latitude,longitude,source
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                  name=COALESCE(excluded.name,airports.name),
                  city_code=COALESCE(excluded.city_code,airports.city_code),
                  city_name=COALESCE(excluded.city_name,airports.city_name),
                  country_code=COALESCE(excluded.country_code,airports.country_code),
                  icao_code=COALESCE(excluded.icao_code,airports.icao_code),
                  latitude=COALESCE(excluded.latitude,airports.latitude),
                  longitude=COALESCE(excluded.longitude,airports.longitude),
                  source=excluded.source
                """,
                (
                    code, name, city_code, city_name, country_code,
                    icao_code, latitude, longitude, source,
                ),
            )
        self.add_alias(code, "airport", code, source=source)
        if name:
            self.add_alias(name, "airport", code, source=source)

    def upsert_city(
        self,
        *,
        code: str,
        name: str | None = None,
        country_code: str | None = None,
        source: str = "sabre",
    ) -> None:
        code = code.upper()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cities(code,name,country_code,source)
                VALUES(?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                  name=COALESCE(excluded.name,cities.name),
                  country_code=COALESCE(excluded.country_code,cities.country_code),
                  source=excluded.source
                """,
                (code, name, country_code, source),
            )
        self.add_alias(code, "city", code, source=source)
        if name:
            self.add_alias(name, "city", code, source=source)

    def upsert_airline(
        self,
        *,
        code: str,
        name: str | None = None,
        icao_code: str | None = None,
        country: str | None = None,
        active: bool | None = None,
        source: str = "sabre",
    ) -> None:
        code = code.upper()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO airlines(code,name,icao_code,country,active,source)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                  name=COALESCE(excluded.name,airlines.name),
                  icao_code=COALESCE(excluded.icao_code,airlines.icao_code),
                  country=COALESCE(excluded.country,airlines.country),
                  active=COALESCE(excluded.active,airlines.active),
                  source=excluded.source
                """,
                (
                    code, name, icao_code, country,
                    None if active is None else int(active), source,
                ),
            )
        self.add_alias(code, "airline", code, source=source)
        if name:
            self.add_alias(name, "airline", code, source=source)

    def add_alias(
        self,
        alias: str,
        entity_type: str,
        code: str,
        *,
        source: str = "seed",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO aliases(alias_folded,entity_type,code,alias_original,source)
                VALUES(?,?,?,?,?)
                """,
                (fold(alias), entity_type, code.upper(), alias, source),
            )

    def resolve_exact(self, text: str, entity_type: str) -> list[str]:
        needle = fold(text)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT code
                FROM aliases
                WHERE alias_folded=? AND entity_type=?
                ORDER BY code
                """,
                (needle, entity_type),
            ).fetchall()
        return [row["code"] for row in rows]

    def alias_records(self, entity_type: str) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT alias_original, code, source
                FROM aliases
                WHERE entity_type=?
                ORDER BY LENGTH(alias_original) DESC, alias_original
                """,
                (entity_type,),
            ).fetchall()
        return [
            {
                "alias": row["alias_original"],
                "code": row["code"],
                "source": row["source"],
            }
            for row in rows
        ]

    def aliases(self, entity_type: str) -> list[tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT alias_original, code
                FROM aliases
                WHERE entity_type=?
                ORDER BY LENGTH(alias_original) DESC, alias_original
                """,
                (entity_type,),
            ).fetchall()
        return [(row["alias_original"], row["code"]) for row in rows]

    def airports_for_city(self, city_code: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT code FROM airports WHERE city_code=? ORDER BY code",
                (city_code.upper(),),
            ).fetchall()
        return [row["code"] for row in rows]

    def delete_source(self, source: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM aliases WHERE source=?", (source,))
            conn.execute("DELETE FROM airports WHERE source=?", (source,))
            conn.execute("DELETE FROM airlines WHERE source=?", (source,))

    def airport(self, code: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM airports WHERE code=?", (code.upper(),)
            ).fetchone()
        return dict(row) if row else None

    def airline(self, code: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM airlines WHERE code=?", (code.upper(),)
            ).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("airports", "cities", "airlines", "aliases")
            }


def seed_reference_data(repo: ReferenceRepository) -> None:
    airports = [
        ("EZE","Ezeiza","BUE","Buenos Aires","AR"),
        ("AEP","Aeroparque","BUE","Buenos Aires","AR"),
        ("COR","Córdoba","COR","Córdoba","AR"),
        ("MDZ","Mendoza","MDZ","Mendoza","AR"),
        ("BRC","Bariloche","BRC","Bariloche","AR"),
        ("ROS","Rosario","ROS","Rosario","AR"),
        ("SLA","Salta","SLA","Salta","AR"),
        ("TUC","Tucumán","TUC","Tucumán","AR"),
        ("IGR","Iguazú","IGR","Iguazú","AR"),
        ("FTE","El Calafate","FTE","El Calafate","AR"),
        ("GRU","Guarulhos","SAO","São Paulo","BR"),
        ("CGH","Congonhas","SAO","São Paulo","BR"),
        ("VCP","Viracopos","SAO","São Paulo","BR"),
        ("GIG","Galeão","RIO","Rio de Janeiro","BR"),
        ("SDU","Santos Dumont","RIO","Rio de Janeiro","BR"),
        ("MIA","Miami International","MIA","Miami","US"),
        ("JFK","John F. Kennedy","NYC","New York","US"),
        ("LGA","LaGuardia","NYC","New York","US"),
        ("EWR","Newark","NYC","New York","US"),
        ("MAD","Adolfo Suárez Madrid-Barajas","MAD","Madrid","ES"),
        ("BCN","Barcelona-El Prat","BCN","Barcelona","ES"),
        ("LHR","Heathrow","LON","London","GB"),
        ("LGW","Gatwick","LON","London","GB"),
        ("LCY","London City","LON","London","GB"),
        ("STN","Stansted","LON","London","GB"),
        ("CDG","Charles de Gaulle","PAR","Paris","FR"),
        ("ORY","Orly","PAR","Paris","FR"),
        ("FCO","Fiumicino","ROM","Rome","IT"),
        ("CIA","Ciampino","ROM","Rome","IT"),
        ("MXP","Malpensa","MIL","Milan","IT"),
        ("LIN","Linate","MIL","Milan","IT"),
        ("NRT","Narita","TYO","Tokyo","JP"),
        ("HND","Haneda","TYO","Tokyo","JP"),
        ("KIX","Kansai","OSA","Osaka","JP"),
        ("ITM","Itami","OSA","Osaka","JP"),
        ("ORD","O'Hare","CHI","Chicago","US"),
        ("MDW","Midway","CHI","Chicago","US"),
        ("IAD","Dulles","WAS","Washington","US"),
        ("DCA","Reagan National","WAS","Washington","US"),
        ("BWI","Baltimore/Washington","WAS","Washington","US"),
        ("YYZ","Toronto Pearson","YTO","Toronto","CA"),
        ("YTZ","Billy Bishop","YTO","Toronto","CA"),
        ("PEK","Beijing Capital","BJS","Beijing","CN"),
        ("PKX","Beijing Daxing","BJS","Beijing","CN"),
        ("PVG","Shanghai Pudong","SHA","Shanghai","CN"),
        ("SHA","Shanghai Hongqiao","SHA","Shanghai","CN"),
        ("ICN","Incheon","SEL","Seoul","KR"),
        ("GMP","Gimpo","SEL","Seoul","KR"),
        ("ARN","Arlanda","STO","Stockholm","SE"),
        ("BMA","Bromma","STO","Stockholm","SE"),
        ("LJU","Ljubljana Jože Pučnik Airport","LJU","Ljubljana","SI"),
    ]
    for code,name,city_code,city_name,country in airports:
        existing = repo.airport(code)
        repo.upsert_airport(
            code=code,
            name=name if existing is None else None,
            city_code=city_code,
            city_name=city_name,
            country_code=country,
            source=(existing or {}).get("source", "seed"),
        )
        repo.upsert_city(code=city_code,name=city_name,country_code=country,source="seed")

    airlines = [
        ("AA","American Airlines"),("AR","Aerolíneas Argentinas"),("LA","LATAM Airlines"),
        ("G3","GOL Linhas Aéreas"),("IB","Iberia"),("UA","United Airlines"),
        ("DL","Delta Air Lines"),("AV","Avianca"),("CM","Copa Airlines"),
        ("LH","Lufthansa"),("AF","Air France"),("KL","KLM"),
        ("TK","Turkish Airlines"),("UX","Air Europa"),("BA","British Airways"),
        ("EK","Emirates"),("QR","Qatar Airways"),("AC","Air Canada"),
        ("AZ","ITA Airways"),("LX","SWISS"),("OS","Austrian Airlines"),
    ]
    for code,name in airlines:
        existing = repo.airline(code)
        if existing is None:
            repo.upsert_airline(code=code,name=name,source="seed")
        else:
            # Preserve imported metadata/source; aliases are already in the DB.
            repo.add_alias(code, "airline", code, source=existing.get("source") or "seed")
            repo.add_alias(name, "airline", code, source="seed")

    special_aliases = [
        ("aerolineas","airline","AR"),("aerolineas argentinas","airline","AR"),
        ("american","airline","AA"),("latam","airline","LA"),("gol","airline","G3"),
        ("gol linhas","airline","G3"),("turkish","airline","TK"),
        ("air europa","airline","UX"),("british","airline","BA"),
        ("qatar","airline","QR"),("emirates","airline","EK"),
        ("buenos aires","city","BUE"),("sao paulo","city","SAO"),
        ("san pablo","city","SAO"),("rio","city","RIO"),("nueva york","city","NYC"),
        ("new york","city","NYC"),("londres","city","LON"),("paris","city","PAR"),
        ("guarulhos","airport","GRU"),("ezeiza","airport","EZE"),
        ("aeroparque","airport","AEP"),
    ]
    for alias,kind,code in special_aliases:
        repo.add_alias(alias,kind,code,source="seed")


_repository: ReferenceRepository | None = None


def get_reference_repository() -> ReferenceRepository:
    global _repository
    if _repository is None:
        _repository = ReferenceRepository()
        seed_reference_data(_repository)
    return _repository
