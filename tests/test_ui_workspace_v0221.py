from pathlib import Path


HTML = Path("app/web/index.html")


def source():
    return HTML.read_text(encoding="utf-8")


def test_workspace_replaces_single_preview():
    html = source()
    assert 'id="artifactSection"' in html
    assert 'id="artifactStack"' in html
    assert 'id="previewSection"' not in html
    assert 'getElementById("previewBody")' not in html
    assert 'getElementById("previewTitle")' not in html


def test_outputs_are_accumulated_and_collapsible():
    html = source()
    assert "const artifactsByQuote = {}" in html
    assert "function appendArtifact(" in html
    assert "function renderArtifacts(" in html
    assert '<details class="artifact-item"' in html
    assert 'type:"whatsapp"' in html
    assert 'type:"email"' in html
    assert 'type:"rules"' in html
    assert 'type:"reprice"' in html


def test_history_has_search_and_larger_workspace():
    html = source()
    assert 'id="historySearch"' in html
    assert "function renderHistory(" in html
    assert 'api("/quotes?limit=200")' in html
    assert "JSON.stringify(q).toLowerCase().includes(needle)" in html


def test_existing_commercial_price_rendering_is_not_removed():
    html = source()
    assert "function farePriceHtml(" in html
    assert "passenger_prices" in html
    assert "q1_amount" in html
    assert "Q1 total incluido" in html
