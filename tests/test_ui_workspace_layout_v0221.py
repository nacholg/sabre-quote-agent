from pathlib import Path


def test_workspace_starts_at_top_and_sidebar_does_not_grow_page():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "margin:0 auto;align-self:start" in html
    assert "position:sticky;top:0;height:calc(100vh - 62px)" in html
    assert "grid-template-columns:300px minmax(0,1fr)" in html
    assert "main{padding:24px;max-width:1450px" in html


def test_mobile_layout_releases_sticky_sidebar():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "position:static;height:auto;max-height:42vh;overflow:auto" in html
