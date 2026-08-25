from pathlib import Path

HTML = Path("app/web/index.html").read_text(encoding="utf-8")


def test_v029b_official_logo_and_mode_switch_are_present():
    assert "Patagonik Travel &amp; Service" in HTML
    assert 'id="guidedModeBtn"' in HTML
    assert 'id="expertModeBtn"' in HTML
    assert "Modo guiado" in HTML
    assert "Modo experto" in HTML


def test_v029b_guided_form_has_core_flight_fields():
    for element_id in (
        "guidedOrigin",
        "guidedDestination",
        "guidedDeparture",
        "guidedReturn",
        "guidedAdults",
        "guidedCabin",
        "guidedCurrency",
        "guidedFare",
        "guidedDirect",
    ):
        assert f'id="{element_id}"' in HTML


def test_v029b_guided_mode_reuses_existing_agent_quote_flow():
    assert "function buildGuidedPrompt()" in HTML
    assert "async function runActiveQuote()" in HTML
    assert "await runQuote();" in HTML
    assert 'id="prompt"' in HTML
    assert 'id="environment"' in HTML


def test_v029b_beginner_help_and_examples_are_operational():
    assert 'id="helpPanel"' in HTML
    assert "function toggleHelpPanel()" in HTML
    assert "function applyPromptExample(text)" in HTML
    assert "Si nunca cotizaste" in HTML
    assert "Si trabajás con Sabre" in HTML


def test_v029b_keeps_read_only_runtime_and_current_workflow_controls():
    for element_id in (
        "runtimeBadge",
        "quoteSection",
        "modifyPrompt",
        "modifyBtn",
        "clientName",
        "clientReference",
        "quoteNotes",
        "options",
    ):
        assert f'id="{element_id}"' in HTML


def test_v029b_results_match_reference_table_layout():
    assert 'class="results-table-header"' in HTML
    for label in (
        "Opción",
        "Itinerario",
        "Aerolínea",
        "Tiempos",
        "Paradas",
        "Tarifa",
        "Equipaje",
        "Cambios / Devoluciones",
        "Precio total",
    ):
        assert label in HTML
    assert "function renderResultLegs(segments)" in HTML
    assert "function groupResultSegmentsByLeg(segments,legs)" in HTML
    assert "function resultStopMeta(segments)" in HTML
    assert "function toggleResultDetails(rank)" in HTML
    assert 'class="primary-result-badge ${badge.key}"' in HTML


def test_v029b_results_use_one_primary_merit_badge():
    assert "function resultBadgeMeta(labels)" in HTML
    assert 'list.includes("recommended")' in HTML
    assert 'list.includes("lowest_price")' in HTML
    assert 'list.includes("fastest")' in HTML
    assert 'list.includes("fewest_stops")' in HTML


def test_v029b_results_group_segments_by_requested_legs():
    assert "groupResultSegmentsByLeg(segments,legs)" in HTML
    assert "currentCommercialQuote?.legs||[]" in HTML
    assert "renderResultLegs(segmentList)" in HTML
