from pathlib import Path


def test_interpretation_prefers_canonical_legs():
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    assert 'const legs=req.legs||[];' in html
    assert 'items.push(["Itinerario",itinerary]);' in html
    assert 'leg.origin' in html
    assert 'leg.destination' in html
    assert 'leg.departure_date' in html


def test_interpretation_keeps_legacy_route_fallback():
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    assert '["Ruta",`${req.origin||""} → ${req.destination||""}`]' in html
    assert '["Fechas",`${req.departure_date||""}${req.return_date?" / "+req.return_date:""}`]' in html
