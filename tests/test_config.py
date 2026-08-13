import pytest
from pydantic import ValidationError

from aegis.config import Settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test123")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.keeperhub_api_key == "kh_test123"
    assert str(settings.keeperhub_base_url).rstrip("/") == "https://app.keeperhub.com"
    assert settings.aegis_allowed_chain_ids == (11155111, 84532, 421614)
    assert settings.aegis_log_level == "INFO"
    assert settings.aegis_log_format == "text"


def test_rejects_non_kh_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPERHUB_API_KEY", "sk_not_a_keeperhub_key")
    with pytest.raises(ValidationError, match="kh_"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_rejects_mainnet_chain_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test123")
    monkeypatch.setenv("AEGIS_ALLOWED_CHAIN_IDS", "1,11155111")
    with pytest.raises(ValidationError, match="mainnet"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_rejects_invalid_log_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test123")
    monkeypatch.setenv("AEGIS_LOG_FORMAT", "yaml")
    with pytest.raises(ValidationError, match="AEGIS_LOG_FORMAT"):
        Settings(_env_file=None)  # type: ignore[call-arg]
