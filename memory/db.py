"""Esquema y conexión SQLite de YacaloX AI.

SQLite viene en la biblioteca estándar de Python: sin dependencias externas ni
servidor. El archivo vive en ``data/yacalox.db`` (excluido de git).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id, id);

CREATE TABLE IF NOT EXISTS tool_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_log_created ON tool_log(created_at);

-- Fase 2: cuota, caché HTTP y datos públicos de YouTube -----------------------

CREATE TABLE IF NOT EXISTS api_quota (
    day TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    units INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, endpoint)
);

CREATE TABLE IF NOT EXISTS http_cache (
    cache_key TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_http_cache_expires ON http_cache(expires_at);

CREATE TABLE IF NOT EXISTS youtube_videos (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT,
    channel_title TEXT,
    title TEXT NOT NULL,
    description TEXT,
    published_at TEXT,
    duration_seconds INTEGER,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    thumbnail_url TEXT,
    source_query TEXT,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON youtube_videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_published ON youtube_videos(published_at);

CREATE TABLE IF NOT EXISTS youtube_channels (
    channel_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    published_at TEXT,
    subscriber_count INTEGER,
    video_count INTEGER,
    view_count INTEGER,
    thumbnail_url TEXT,
    uploads_playlist_id TEXT,
    collected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS youtube_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    result_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_youtube_queries_created ON youtube_queries(created_at);
"""


def utc_now() -> str:
    """Marca de tiempo ISO-8601 en UTC (ordenable lexicográficamente)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """Conexión SQLite compartida con bloqueo (segura para hilos simples)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def schema_version(self) -> int:
        rows = self.query("SELECT value FROM meta WHERE key='schema_version'")
        return int(rows[0]["value"]) if rows else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
