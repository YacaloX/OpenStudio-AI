"""Configuración validada de YacaloX AI.

Toma sus valores de variables de entorno (con soporte de un archivo `.env`
local) y valida todo antes de que arranque el resto del sistema.

Uso::

    from config import load_settings
    settings = load_settings()
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.errors import ConfigError

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"

VALID_PROVIDERS = ("ollama", "fake")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Carga un archivo `.env` en el entorno sin sobreescribir variables ya definidas.

    Formato soportado: ``KEY=valor``, comentarios con ``#`` y líneas vacías.
    Las comillas simples o dobles alrededor del valor se eliminan.
    """
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}='{raw}' no es un número entero válido.") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name}={value} fuera del rango [{minimum}, {maximum}].")
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}='{raw}' no es un número válido.") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name}={value} fuera del rango [{minimum}, {maximum}].")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name}='{raw}' no es un booleano (usa true/false).")


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable y ya validada."""

    provider: str
    ollama_url: str
    model: str
    timeout: float
    think: bool
    max_tool_iterations: int
    history_window: int
    temperature: float
    log_level: str
    data_dir: Path
    db_path: Path
    youtube_api_key: str | None
    youtube_client_secrets: Path

    @classmethod
    def from_env(cls) -> Settings:
        provider = _env_str("YACALOX_PROVIDER", "ollama").lower()
        if provider not in VALID_PROVIDERS:
            raise ConfigError(
                f"YACALOX_PROVIDER='{provider}' no soportado. Usa uno de: {', '.join(VALID_PROVIDERS)}."
            )

        log_level = _env_str("YACALOX_LOG_LEVEL", "INFO").upper()
        if log_level not in VALID_LOG_LEVELS:
            raise ConfigError(
                f"YACALOX_LOG_LEVEL='{log_level}' inválido. Usa uno de: {', '.join(VALID_LOG_LEVELS)}."
            )

        model = _env_str("YACALOX_MODEL", "qwen3:1.7b")
        if not model or any(c.isspace() for c in model):
            raise ConfigError("YACALOX_MODEL no puede estar vacío ni contener espacios.")

        ollama_url = _env_str("YACALOX_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        if not ollama_url.startswith(("http://", "https://")):
            raise ConfigError(f"YACALOX_OLLAMA_URL='{ollama_url}' debe empezar por http:// o https://.")

        data_dir_raw = _env_str("YACALOX_DATA_DIR", "data")
        data_dir = Path(data_dir_raw)
        if not data_dir.is_absolute():
            data_dir = ROOT_DIR / data_dir

        client_secrets_raw = _env_str("YOUTUBE_CLIENT_SECRETS", "data/client_secret.json")
        client_secrets = Path(client_secrets_raw)
        if not client_secrets.is_absolute():
            client_secrets = ROOT_DIR / client_secrets

        youtube_api_key = os.environ.get("YOUTUBE_API_KEY", "").strip() or None

        settings = cls(
            provider=provider,
            ollama_url=ollama_url,
            model=model,
            timeout=_env_float("YACALOX_TIMEOUT", 120.0, minimum=1.0, maximum=900.0),
            think=_env_bool("YACALOX_THINK", False),
            max_tool_iterations=_env_int(
                "YACALOX_MAX_TOOL_ITERATIONS", 6, minimum=1, maximum=32
            ),
            history_window=_env_int("YACALOX_HISTORY_WINDOW", 12, minimum=2, maximum=200),
            temperature=_env_float("YACALOX_TEMPERATURE", 0.4, minimum=0.0, maximum=2.0),
            log_level=log_level,
            data_dir=data_dir,
            db_path=data_dir / "yacalox.db",
            youtube_api_key=youtube_api_key,
            youtube_client_secrets=client_secrets,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.data_dir.exists() and not self.data_dir.is_dir():
            raise ConfigError(f"YACALOX_DATA_DIR apunta a un archivo, no a una carpeta: {self.data_dir}")
        if self.provider == "ollama" and not self.model:
            raise ConfigError("Con provider=ollama es obligatorio YACALOX_MODEL.")

    def ensure_dirs(self) -> None:
        """Crea los directorios locales necesarios (idempotente)."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def summary(self, *, redact: bool = True) -> dict[str, object]:
        """Resumen no sensible para mostrar en la CLI o registrar."""
        return {
            "provider": self.provider,
            "model": self.model,
            "ollama_url": self.ollama_url,
            "timeout_s": self.timeout,
            "temperature": self.temperature,
            "max_tool_iterations": self.max_tool_iterations,
            "history_window": self.history_window,
            "data_dir": str(self.data_dir),
            "db_path": str(self.db_path),
            "youtube_api_key": ("configurada" if self.youtube_api_key else "no configurada"),
            "youtube_client_secrets": str(self.youtube_client_secrets),
            "redacted": redact,
        }


_cache: Settings | None = None


def load_settings(*, reload: bool = False) -> Settings:
    """Carga (y cachea) la configuración validada."""
    global _cache
    if _cache is None or reload:
        load_env()
        _cache = Settings.from_env()
    return _cache
