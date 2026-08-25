from fastapi.testclient import TestClient

from app.main import app


def test_root_redirects_to_web_app():
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/")
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/app"


def test_web_app_loads():
    with TestClient(app) as client:
        response = client.get("/app")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Patagonik Travel" in response.text
    assert "Patagonik Travel &amp; Service" in response.text
    assert "Sabre BFM" in response.text
    assert "/agent/quote" in response.text
    assert "/fare-rules" in response.text
    assert "/whatsapp" in response.text
