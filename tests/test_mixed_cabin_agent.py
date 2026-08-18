from fastapi.testclient import TestClient

from app.main import app


def test_mixed_cabin_agent_does_not_execute_incorrect_global_cabin_search():
    with TestClient(app) as client:
        response = client.post(
            "/agent/quote",
            json={
                "text": "Cotizame EZE a Ljubljana del 19 al 30 de septiembre, USD, ida business, vuelta premium economy, con devolucion",
                "environment": "cert",
                "execute": True,
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["quote"] is None
    assert body["interpretation"]["search_request"]["outbound_cabin"] == "BUSINESS"
    assert body["interpretation"]["search_request"]["return_cabin"] == "PREMIUM_ECONOMY"
