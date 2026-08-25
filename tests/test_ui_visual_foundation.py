from pathlib import Path

HTML = Path("app/web/index.html").read_text(encoding="utf-8")


def test_v029a_patagonik_branding_is_present():
    assert "Patagonik Travel" in HTML
    assert "Panel de cotización aérea" in HTML
    assert 'class="brand-lockup"' in HTML
    assert 'class="brand-mark"' in HTML


def test_v029a_visual_tokens_include_argentina_and_river_accents():
    assert "--pt-sky:#6ec6f0" in HTML
    assert "--pt-navy:#0f2742" in HTML
    assert "--pt-river:#d3263f" in HTML
    assert "--pt-gold:#f2c94c" in HTML


def test_v029a_keeps_core_operational_controls():
    for element_id in (
        "runtimeBadge",
        "prompt",
        "environment",
        "searchBtn",
        "interpretation",
        "quoteSection",
        "modifyPrompt",
        "modifyBtn",
        "options",
    ):
        assert f'id="{element_id}"' in HTML


def test_v029a_has_responsive_visual_foundation():
    assert "@media(max-width:1100px)" in HTML
    assert "@media(max-width:900px)" in HTML
    assert "@media(max-width:560px)" in HTML
    assert 'class="panel hero-panel"' in HTML
    assert 'class="modify-composer"' in HTML
