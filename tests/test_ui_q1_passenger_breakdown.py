from pathlib import Path


def test_ui_renders_q1_by_passenger_and_total():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "p.q1_amount" in html
    assert "p.q1_total" in html
    assert "Q1 total incluido" in html
    assert "q1Totals.reduce" in html
