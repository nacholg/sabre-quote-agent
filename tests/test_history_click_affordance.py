from pathlib import Path


def _source() -> str:
    return Path("app/web/index.html").read_text(
        encoding="utf-8"
    )


def test_history_items_have_subtle_hover_and_press_feedback():
    source = _source()

    assert ".history-item:hover{" in source
    assert "transform:translateY(-1px);" in source
    assert ".history-item:active{" in source
    assert "scale(.99)" in source
    assert "box-shadow:0 5px 14px" in source


def test_history_items_show_directional_click_affordance():
    source = _source()

    assert '.history-item::after{' in source
    assert 'content:"›";' in source
    assert ".history-item:hover::after{" in source


def test_history_items_are_keyboard_accessible():
    source = _source()

    assert 'role="button"' in source
    assert 'tabindex="0"' in source
    assert "event.key==='Enter'" in source
    assert "event.key===' '" in source


def test_active_history_item_remains_visually_distinct():
    source = _source()

    assert ".history-item.active{" in source
    assert "background:#f7f9ff;" in source
    assert ".history-item.active::after{" in source
