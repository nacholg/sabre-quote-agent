from app.config import get_settings


def _set_required_sabre_env(monkeypatch):
    monkeypatch.setenv("SABRE_ENV", "CERT")
    monkeypatch.setenv("SABRE_ENVIRONMENT", "cert")
    monkeypatch.setenv("SABRE_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("SABRE_TOKEN_TYPE", "password")
    monkeypatch.setenv("SABRE_CLIENT_ID", "test-client")
    monkeypatch.setenv("SABRE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("SABRE_USERNAME", "test-user")
    monkeypatch.setenv("SABRE_PASSWORD", "test-password")
    monkeypatch.setenv("SABRE_PCC", "TEST")


def test_get_settings_falls_back_to_process_environment_without_dotenv(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    _set_required_sabre_env(monkeypatch)

    get_settings.cache_clear()
    settings = get_settings("cert")

    assert settings.sabre_env == "CERT"
    assert settings.sabre_environment == "cert"
    assert settings.sabre_pcc == "TEST"
    assert settings.base_url == "https://example.invalid"

    get_settings.cache_clear()


def test_get_settings_still_uses_local_dotenv_when_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SABRE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SABRE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SABRE_PCC", raising=False)

    (tmp_path / ".env.cert").write_text(
        "\n".join(
            [
                "SABRE_ENV=CERT",
                "SABRE_ENVIRONMENT=cert",
                "SABRE_CLIENT_ID=file-client",
                "SABRE_CLIENT_SECRET=file-secret",
                "SABRE_PCC=FILE",
            ]
        ),
        encoding="utf-8",
    )

    get_settings.cache_clear()
    settings = get_settings("cert")

    assert settings.sabre_env == "CERT"
    assert settings.sabre_environment == "cert"
    assert settings.sabre_pcc == "FILE"
    assert settings.sabre_client_id.get_secret_value() == "file-client"

    get_settings.cache_clear()
