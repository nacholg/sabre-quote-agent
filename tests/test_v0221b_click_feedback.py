from pathlib import Path


def test_buttons_have_click_feedback():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "button:active:not(:disabled)" in html
    assert "@keyframes button-click-feedback" in html
    assert 'event.target.closest("button")' in html
    assert "clicked-feedback" in html
