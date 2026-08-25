from pathlib import Path


def _source() -> str:
    return Path("app/web/index.html").read_text(
        encoding="utf-8"
    )


def test_interpretation_shows_ai_badge_for_hybrid_parser():
    source = _source()

    assert "function interpretationParserMeta(i){" in source
    assert 'parser.includes("hybrid")' in source
    assert 'parser.includes("llm")' in source
    assert 'return {label:"IA",className:"ai"};' in source


def test_interpretation_shows_local_badge_for_deterministic_parser():
    source = _source()

    assert 'parser.includes("deterministic")' in source
    assert 'return {label:"Local",className:"local"};' in source


def test_parser_badge_is_subtle_and_inline():
    source = _source()

    assert ".interpret-heading{" in source
    assert ".parser-badge{" in source
    assert ".parser-badge.ai{" in source
    assert ".parser-badge.local{" in source
    assert 'Interpretación de tu solicitud ${parserBadge}' in source


def test_legacy_interpretation_without_parser_has_no_badge():
    source = _source()

    start = source.index("function interpretationParserMeta(i){")
    end = source.index("function renderInterpretation(i){", start)
    body = source[start:end]

    assert "return null;" in body
