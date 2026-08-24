from pathlib import Path


def test_ui_has_conversational_quote_composer():
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    assert 'id="modifyPrompt"' in html
    assert 'id="modifyBtn"' in html
    assert "Continuar esta cotización" in html
    assert "modifyCurrentQuote()" in html


def test_ui_calls_quote_modify_endpoint():
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    assert "/modify" in html
    assert 'execute:true' in html
    assert "renderModificationChanges" in html


def test_ui_conversational_controls_follow_version_mutability():
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    assert 'id="modifyPrompt"' in html
    assert 'id="modifyBtn"' in html

    prompt_fragment = html.split('id="modifyPrompt"', 1)[1][:220]
    button_fragment = html.split('id="modifyBtn"', 1)[1][:220]

    assert 'data-current-only="true"' in prompt_fragment
    assert 'data-current-only="true"' in button_fragment



def test_ui_modification_changes_show_parser_badge():
    html = Path("app/web/index.html").read_text(encoding="utf-8")
    assert "conversation-hybrid-llm-v1" in html
    assert "Cambios aplicados" in html
    assert "parser-badge" in html



def test_ui_agent_modify_does_not_overwrite_new_quote_prompt():
    html = Path("app/web/index.html").read_text(encoding="utf-8")

    assert 'rec.source==="agent_modify"' in html
    assert 'newQuotePrompt.value=""' in html
