import sqlite3

from scripts.import_reference_data import import_openflights_airlines, import_ourairports
from app.services.reference_repository import ReferenceRepository


OURAIRPORTS_SAMPLE = '''id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,municipality,scheduled_service,gps_code,iata_code,local_code,home_link,wikipedia_link,keywords
1,SAEZ,large_airport,Ministro Pistarini International Airport,-34.8222,-58.5358,67,SA,AR,AR-B,Ezeiza,yes,SAEZ,EZE,EZE,,,Ezeiza Airport
2,SBGR,large_airport,Guarulhos International Airport,-23.4356,-46.4731,2459,SA,BR,BR-SP,Guarulhos,yes,SBGR,GRU,GRU,,,Cumbica
3,SBSP,large_airport,Congonhas Airport,-23.6261,-46.6564,2631,SA,BR,BR-SP,São Paulo,yes,SBSP,CGH,CGH,,,
4,SBMT,medium_airport,Campo de Marte Airport,-23.5091,-46.6378,2368,SA,BR,BR-SP,São Paulo,no,SBMT,,SBMT,,,
'''

OPENFLIGHTS_SAMPLE = '''1,"American Airlines","\\N","AA","AAL","AMERICAN","United States","Y"
2,"Turkish Airlines","Turkish","TK","THY","TURKISH","Turkey","Y"
3,"Old Airline","\\N","ZZ","OLD","OLD","Nowhere","N"
'''


def test_import_ourairports(tmp_path):
    repo=ReferenceRepository(tmp_path/"reference.db")
    stats=import_ourairports(repo,OURAIRPORTS_SAMPLE)
    assert stats["airports"] == 3
    assert repo.resolve_exact("Ezeiza Airport","airport") == ["EZE"]
    assert repo.resolve_exact("Guarulhos","airport") == ["GRU"]
    assert repo.airport("GRU")["icao_code"] == "SBGR"


def test_import_openflights_active_only(tmp_path):
    repo=ReferenceRepository(tmp_path/"reference.db")
    stats=import_openflights_airlines(repo,OPENFLIGHTS_SAMPLE)
    assert stats["airlines"] == 2
    assert stats["skipped_inactive"] == 1
    assert repo.resolve_exact("Turkish","airline") == ["TK"]
    assert repo.airline("TK")["icao_code"] == "THY"


def test_reference_db_migrates_from_0172_schema(tmp_path):
    db=tmp_path/"reference.db"
    conn=sqlite3.connect(db)
    conn.executescript("""
    CREATE TABLE airports (
      code TEXT PRIMARY KEY,name TEXT,city_code TEXT,city_name TEXT,country_code TEXT,
      source TEXT NOT NULL DEFAULT 'seed'
    );
    CREATE TABLE cities (
      code TEXT PRIMARY KEY,name TEXT,country_code TEXT,source TEXT NOT NULL DEFAULT 'seed'
    );
    CREATE TABLE airlines (
      code TEXT PRIMARY KEY,name TEXT,source TEXT NOT NULL DEFAULT 'seed'
    );
    CREATE TABLE aliases (
      alias_folded TEXT NOT NULL,entity_type TEXT NOT NULL,code TEXT NOT NULL,
      alias_original TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'seed',
      PRIMARY KEY (alias_folded,entity_type,code)
    );
    """)
    conn.commit()
    conn.close()

    repo=ReferenceRepository(db)
    repo.upsert_airport(code="EZE",icao_code="SAEZ",latitude=-34.8,longitude=-58.5)
    repo.upsert_airline(code="AA",icao_code="AAL",country="United States",active=True)
    assert repo.airport("EZE")["icao_code"] == "SAEZ"
    assert repo.airline("AA")["active"] == 1
