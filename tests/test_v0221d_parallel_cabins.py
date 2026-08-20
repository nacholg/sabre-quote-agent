from pathlib import Path

def source():
    return Path("app/services/quote_service.py").read_text(encoding="utf-8")

def test_parallel_primary_cabin_searches():
    src = source()
    assert "asyncio.gather(" in src
    assert "asyncio.Semaphore(3)" in src
    assert "async def _timed_primary_bfm_search(" in src
    assert "BFM cabin batch currency=" in src

def test_wall_and_service_timings_are_separate():
    src = source()
    assert "_bfm_wall_seconds = 0.0" in src
    assert "BFM wall=" in src
    assert "BFM service=" in src

def test_persistence_remains():
    src = source()
    assert "quote_id = repository.create(request=request, response=response)" in src
