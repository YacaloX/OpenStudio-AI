"""Caché persistente de respuestas de la API de YouTube.

Evita gastar cuota repitiendo la misma consulta. Se guarda el JSON ya
normalizado (sin la clave de API) con fecha de caducidad. La clave incluye
todos los parámetros relevantes, así que dos peticiones distintas nunca
comparten entrada.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from memory.db import Database

_IGNORED_PARAMS = {"key"}


def cache_key(endpoint: str, params: dict[str, Any]) -> str:
    filtered = {k: v for k, v in params.items() if k not in _IGNORED_PARAMS}
    payload = json.dumps({"endpoint": endpoint, "params": filtered}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class HttpCache:
    """Caché TTL sobre la tabla ``http_cache``."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str) -> dict[str, Any] | None:
        rows = self.db.query("SELECT payload, expires_at FROM http_cache WHERE cache_key=?", (key,))
        if not rows:
            return None
        expires_at = str(rows[0]["expires_at"])
        if expires_at <= datetime.now(UTC).isoformat(timespec="seconds"):
            self.db.execute("DELETE FROM http_cache WHERE cache_key=?", (key,))
            return None
        try:
            payload = json.loads(rows[0]["payload"])
        except (TypeError, json.JSONDecodeError):
            self.db.execute("DELETE FROM http_cache WHERE cache_key=?", (key,))
            return None
        return payload if isinstance(payload, dict) else None

    def set(self, key: str, endpoint: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=int(ttl_seconds))
        self.db.execute(
            "INSERT INTO http_cache(cache_key, endpoint, payload, created_at, expires_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,"
            " created_at=excluded.created_at, expires_at=excluded.expires_at",
            (
                key,
                endpoint,
                json.dumps(payload, ensure_ascii=False),
                now.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"),
            ),
        )

    def purge_expired(self) -> int:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        cursor = self.db.execute("DELETE FROM http_cache WHERE expires_at <= ?", (now,))
        return cursor.rowcount
