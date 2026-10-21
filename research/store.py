"""Persistencia de los datos públicos recopilados de YouTube.

Los vídeos y canales se guardan en SQLite con la fecha de recolección para
poder distinguir «dato de hace tres semanas» de «dato de hoy». Las métricas
privadas (CTR, retención, ingresos) no existen en esta capa.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from memory.db import Database, utc_now
from youtube.models import Channel, Video


class ResearchStore:
    """Repositorio local de vídeos, canales y búsquedas."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # --------------------------------------------------------------- writes
    def upsert_videos(self, videos: Iterable[Video], *, source_query: str | None = None) -> int:
        now = utc_now()
        saved = 0
        for video in videos:
            self.db.execute(
                """
                INSERT INTO youtube_videos(
                    video_id, channel_id, channel_title, title, description, published_at,
                    duration_seconds, view_count, like_count, comment_count, thumbnail_url,
                    source_query, collected_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(video_id) DO UPDATE SET
                    channel_id=excluded.channel_id,
                    channel_title=excluded.channel_title,
                    title=excluded.title,
                    description=excluded.description,
                    published_at=excluded.published_at,
                    duration_seconds=excluded.duration_seconds,
                    view_count=excluded.view_count,
                    like_count=excluded.like_count,
                    comment_count=excluded.comment_count,
                    thumbnail_url=excluded.thumbnail_url,
                    source_query=COALESCE(excluded.source_query, youtube_videos.source_query),
                    collected_at=excluded.collected_at
                """,
                (
                    video.video_id,
                    video.channel_id,
                    video.channel_title,
                    video.title,
                    video.description,
                    video.published_at,
                    video.duration_seconds,
                    video.view_count,
                    video.like_count,
                    video.comment_count,
                    video.thumbnail_url,
                    source_query or video.source_query,
                    now,
                ),
            )
            saved += 1
        return saved

    def upsert_channels(self, channels: Iterable[Channel]) -> int:
        now = utc_now()
        saved = 0
        for channel in channels:
            self.db.execute(
                """
                INSERT INTO youtube_channels(
                    channel_id, title, description, published_at, subscriber_count,
                    video_count, view_count, thumbnail_url, uploads_playlist_id, collected_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    title=excluded.title,
                    description=excluded.description,
                    published_at=excluded.published_at,
                    subscriber_count=excluded.subscriber_count,
                    video_count=excluded.video_count,
                    view_count=excluded.view_count,
                    thumbnail_url=excluded.thumbnail_url,
                    uploads_playlist_id=excluded.uploads_playlist_id,
                    collected_at=excluded.collected_at
                """,
                (
                    channel.channel_id,
                    channel.title,
                    channel.description,
                    channel.published_at,
                    channel.subscriber_count,
                    channel.video_count,
                    channel.view_count,
                    channel.thumbnail_url,
                    channel.uploads_playlist_id,
                    now,
                ),
            )
            saved += 1
        return saved

    def log_query(self, query: str, endpoint: str, params: dict, result_count: int) -> int:
        cursor = self.db.execute(
            "INSERT INTO youtube_queries(query, endpoint, params, result_count, created_at)"
            " VALUES(?,?,?,?,?)",
            (query, endpoint, json.dumps(params, ensure_ascii=False, default=str), result_count, utc_now()),
        )
        return int(cursor.lastrowid or 0)

    # ---------------------------------------------------------------- reads
    def video(self, video_id: str) -> Video | None:
        rows = self.db.query("SELECT * FROM youtube_videos WHERE video_id=?", (video_id,))
        return _video_from_row(rows[0]) if rows else None

    def videos(self, *, limit: int = 50, channel_id: str | None = None) -> list[Video]:
        limit = max(1, min(int(limit), 500))
        if channel_id:
            rows = self.db.query(
                "SELECT * FROM youtube_videos WHERE channel_id=? ORDER BY published_at DESC LIMIT ?",
                (channel_id, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM youtube_videos ORDER BY collected_at DESC, published_at DESC LIMIT ?",
                (limit,),
            )
        return [_video_from_row(row) for row in rows]

    def channels(self, *, limit: int = 50) -> list[Channel]:
        rows = self.db.query(
            "SELECT * FROM youtube_channels ORDER BY collected_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        )
        return [_channel_from_row(row) for row in rows]

    def recent_queries(self, *, limit: int = 10) -> list[dict[str, object]]:
        rows = self.db.query(
            "SELECT * FROM youtube_queries ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 100)),)
        )
        return [
            {
                "id": int(row["id"]),
                "query": str(row["query"]),
                "endpoint": str(row["endpoint"]),
                "params": str(row["params"]),
                "result_count": int(row["result_count"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        videos = self.db.query("SELECT COUNT(*) AS n FROM youtube_videos")[0]["n"]
        channels = self.db.query("SELECT COUNT(*) AS n FROM youtube_channels")[0]["n"]
        queries = self.db.query("SELECT COUNT(*) AS n FROM youtube_queries")[0]["n"]
        return {"videos": int(videos), "channels": int(channels), "queries": int(queries)}


def _video_from_row(row) -> Video:
    return Video(
        video_id=str(row["video_id"]),
        title=str(row["title"]),
        channel_id=row["channel_id"],
        channel_title=row["channel_title"],
        description=row["description"],
        published_at=row["published_at"],
        duration_seconds=row["duration_seconds"],
        view_count=row["view_count"],
        like_count=row["like_count"],
        comment_count=row["comment_count"],
        thumbnail_url=row["thumbnail_url"],
        source_query=row["source_query"],
    )


def _channel_from_row(row) -> Channel:
    return Channel(
        channel_id=str(row["channel_id"]),
        title=str(row["title"]),
        description=row["description"],
        published_at=row["published_at"],
        subscriber_count=row["subscriber_count"],
        video_count=row["video_count"],
        view_count=row["view_count"],
        thumbnail_url=row["thumbnail_url"],
        uploads_playlist_id=row["uploads_playlist_id"],
    )
