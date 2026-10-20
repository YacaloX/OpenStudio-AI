"""Utilidades comunes de las pruebas: configuración y base de datos temporales."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings, load_env  # noqa: E402
from memory.store import MemoryStore  # noqa: E402
from tools import build_default_registry  # noqa: E402

ENV_VARS = (
    "YACALOX_PROVIDER",
    "YACALOX_MODEL",
    "YACALOX_OLLAMA_URL",
    "YACALOX_TIMEOUT",
    "YACALOX_THINK",
    "YACALOX_MAX_TOOL_ITERATIONS",
    "YACALOX_HISTORY_WINDOW",
    "YACALOX_TEMPERATURE",
    "YACALOX_LOG_LEVEL",
    "YACALOX_DATA_DIR",
    "YOUTUBE_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path) -> Iterator[None]:
    """Aísla las pruebas del `.env` real y del entorno del desarrollador."""
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("YACALOX_DATA_DIR", str(tmp_path / "data"))
    yield


@pytest.fixture
def settings(tmp_path) -> Settings:
    monkey_data = tmp_path / "data"
    monkey_data.mkdir(parents=True, exist_ok=True)
    return Settings(
        provider="fake",
        ollama_url="http://127.0.0.1:11434",
        model="fake-model",
        timeout=5.0,
        think=False,
        max_tool_iterations=4,
        history_window=8,
        temperature=0.4,
        log_level="INFO",
        data_dir=monkey_data,
        db_path=monkey_data / "test.db",
        youtube_api_key=None,
        youtube_client_secrets=monkey_data / "client_secret.json",
    )


@pytest.fixture
def store(tmp_path) -> Iterator[MemoryStore]:
    memory = MemoryStore(tmp_path / "memory_test.db")
    yield memory
    memory.close()


@pytest.fixture
def registry(store, settings):
    return build_default_registry(store, settings)


def test_root_has_config_module():
    assert (ROOT / "config.py").is_file()
    assert (ROOT / "main.py").is_file()


def test_load_env_reads_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comentario\nYACALOX_MODEL="qwen3:1.7b"\nYACALOX_TIMEOUT=30\nVACIA=\nMALA\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("YACALOX_MODEL", raising=False)
    loaded = load_env(env_file)
    assert loaded["YACALOX_MODEL"] == "qwen3:1.7b"
    assert loaded["YACALOX_TIMEOUT"] == "30"
    assert "MALA" not in loaded
    assert os.environ["YACALOX_MODEL"] == "qwen3:1.7b"
