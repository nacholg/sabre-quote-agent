from fastapi.testclient import TestClient
from app.main import app


def test_reference_stats_endpoint():
    with TestClient(app) as client:
        response=client.get("/reference/stats")
    assert response.status_code == 200
    assert response.json()["airports"] >= 25


def test_reference_resolve_endpoint():
    with TestClient(app) as client:
        response=client.get("/reference/resolve",params={"q":"Turkish","type":"airline"})
    assert response.status_code == 200
    assert response.json()["codes"] == ["TK"]
