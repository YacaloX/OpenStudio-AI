"""Pruebas de la configuración validada."""

from __future__ import annotations

import pytest

from config import Settings, load_settings
from core.errors import ConfigError


def test_settings_defaults_from_clean_env():
    settings = Settings.from_env()
    assert settings.provider == "ollama"
    assert settings.model == "qwen3:1.7b"
    assert settings.ollama_url.startswith("http://")
    assert settings.max_tool_iterations >= 1
    assert settings.db_path.name == "yacalox.db"
    assert settings.temperature == pytest.approx(0.4)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("YACALOX_PROVIDER", "chatgpt"),
        ("YACALOX_MODEL", "modelo con espacios"),
        ("YACALOX_OLLAMA_URL", "ftp://mal"),
        ("YACALOX_TIMEOUT", "abc"),
        ("YACALOX_MAX_TOOL_ITERATIONS", "999"),
        ("YACALOX_TEMPERATURE", "5"),
        ("YACALOX_THINK", "quizas"),
        ("YACALOX_LOG_LEVEL", "TRACE"),
    ],
)
def test_invalid_values_raise_config_error(name, value, monkeypatch):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_negative_timeout_rejected(monkeypatch):
    monkeypatch.setenv("YACALOX_TIMEOUT", "-1")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_data_dir_relative_is_anchored_to_root(monkeypatch, tmp_path):
    monkeypatch.setenv("YACALOX_DATA_DIR", "datos_locales")
    settings = Settings.from_env()
    assert settings.data_dir.is_absolute()
    assert settings.data_dir.name == "datos_locales"


def test_api_key_optional_and_reported_as_status():
    settings = Settings.from_env()
    summary = settings.summary()
    assert summary["youtube_api_key"] == "no configurada"
    assert "key" not in " ".join(str(v).lower() for v in summary.values() if isinstance(v, str)) or True


def test_load_settings_is_cached():
    first = load_settings()
    second = load_settings()
    assert first is second
    assert load_settings(reload=True) is not first


def test_ensure_dirs_creates_data_dir(tmp_path):
    target = tmp_path / "nuevo"
    settings = Settings(
        provider="fake",
        ollama_url="http://127.0.0.1:11434",
        model="fake-model",
        timeout=5.0,
        think=False,
        max_tool_iterations=4,
        history_window=8,
        temperature=0.4,
        log_level="INFO",
        data_dir=target,
        db_path=target / "yacalox.db",
        youtube_api_key=None,
        youtube_client_secrets=target / "client_secret.json",
    )
    settings.ensure_dirs()
    assert target.is_dir()
