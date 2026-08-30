from pathlib import Path


def test_auth_reports_real_oauth_refresh_time():
    src = Path("app/sabre/auth.py").read_text(encoding="utf-8")
    assert "[SABRE] OAuth refresh:" in src
    assert "_oauth_started = time.perf_counter()" in src


def test_client_post_reports_token_http_and_json_times():
    src = Path("app/sabre/client.py").read_text(encoding="utf-8")
    assert "[SABRE] token lookup:" in src
    assert "[SABRE] HTTP:" in src
    assert "[SABRE] JSON parse:" in src
    assert "_token_started = time.perf_counter()" in src
    assert "_http_started = time.perf_counter()" in src
    assert "_json_started = time.perf_counter()" in src
