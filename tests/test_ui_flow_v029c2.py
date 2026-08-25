from pathlib import Path

HTML = Path("app/web/index.html").read_text(encoding="utf-8")


def test_v029c2_has_explicit_fourth_conversation_step():
    assert "function installV029c2FourStepFlow()" in HTML
    assert 'flow.className="flow-step-four"' in HTML
    assert '<span class="flow-step-number">4</span>' in HTML
    assert "Continuá la conversación" in HTML


def test_v029c2_moves_existing_actions_without_changing_handlers():
    assert 'text.includes("whatsapp")' in HTML
    assert 'label==="email"' in HTML
    assert 'label.includes("marcar enviada")' in HTML
    assert "primary.appendChild(button);" in HTML
    assert "secondary.appendChild(button);" in HTML


def test_v029c2_has_icon_action_cards():
    assert "function v029c2ActionIcon(kind)" in HTML
    assert "action-whatsapp" in HTML
    assert "action-email" in HTML
    assert "action-sent" in HTML
    assert ".flow-primary-actions{" in HTML
    assert ".flow-action-card{" in HTML


def test_v029c2_primary_result_badges_do_not_overflow():
    assert ".primary-result-badge{" in HTML
    assert "max-width:100%;" in HTML
    assert "text-overflow:ellipsis;" in HTML
    assert "white-space:nowrap!important;" in HTML


def test_v029c2_interpretation_is_compact_and_decorated():
    assert "function decorateInterpretationCards()" in HTML
    assert "function v029c2InterpretIcon(label)" in HTML
    assert ".interpret-card-decorated{" in HTML
    assert ".interpret-icon{" in HTML
    assert "#interpretation .interpret{" in HTML


def test_v029c2_flow_is_responsive():
    assert "@media(max-width:1160px)" in HTML
    assert ".flow-step-four{" in HTML
    assert "grid-template-columns:1fr;" in HTML


def test_v029c2_mutation_observer_is_idempotent():
    assert 'const helperText="Cada cambio crea una nueva versión";' in HTML
    assert "helper && helper.textContent!==helperText" in HTML
    assert "let flowUpdateScheduled=false;" in HTML
    assert "window.requestAnimationFrame(" in HTML
