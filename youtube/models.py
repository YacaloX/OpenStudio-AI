"""Modelos de datos y parsers de la YouTube Data API v3."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)

WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
CHANNEL_URL = "https://www.youtube.com/channel/{channel_id}"


def parse_duration(value: str | None) -> int | None:
    """Convierte una duración ISO-8601 (``PT1H2M3S``) a segundos.

    Devuelve ``None`` si el formato no es reconocible (los directos en curso
    pueden no tener duración).
    """
    if not value:
        return None
    match = _DURATION_RE.match(value.strip())
    if not match:
        return None
    parts = {name: int(raw) if raw else 0 for name, raw in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _best_thumbnail(thumbnails: dict[str, Any] | None) -> str | None:
    if not thumbnails:
        return None
    for size in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbnails.get(size)
        if entry and entry.get("url"):
            return str(entry["url"])
    return None


@dataclass(frozen=True)
class Video:
    """Vídeo público con las métricas que la API expone realmente."""

    video_id: str
    title: str
    channel_id: str | None = None
    channel_title: str | None = None
    description: str | None = None
    published_at: str | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    thumbnail_url: str | None = None
    source_query: str | None = None

    @property
    def url(self) -> str:
        return WATCH_URL.format(video_id=self.video_id)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


@dataclass(frozen=True)
class Channel:
    """Canal público. Las métricas privadas (CTR, retención) no existen aquí."""

    channel_id: str
    title: str
    description: str | None = None
    published_at: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    thumbnail_url: str | None = None
    uploads_playlist_id: str | None = None

    @property
    def url(self) -> str:
        return CHANNEL_URL.format(channel_id=self.channel_id)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


def video_from_api(item: dict[str, Any]) -> Video:
    """Construye un :class:`Video` desde un recurso de ``videos.list``."""
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    details = item.get("contentDetails") or {}
    return Video(
        video_id=str(item.get("id", "")),
        title=str(snippet.get("title") or "(sin título)"),
        channel_id=snippet.get("channelId"),
        channel_title=snippet.get("channelTitle"),
        description=snippet.get("description"),
        published_at=snippet.get("publishedAt"),
        duration_seconds=parse_duration(details.get("duration")),
        view_count=_int_or_none(stats.get("viewCount")),
        like_count=_int_or_none(stats.get("likeCount")),
        comment_count=_int_or_none(stats.get("commentCount")),
        thumbnail_url=_best_thumbnail(snippet.get("thumbnails")),
    )


def channel_from_api(item: dict[str, Any]) -> Channel:
    """Construye un :class:`Channel` desde un recurso de ``channels.list``."""
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    content = item.get("contentDetails") or {}
    related = content.get("relatedPlaylists") or {}
    return Channel(
        channel_id=str(item.get("id", "")),
        title=str(snippet.get("title") or "(sin título)"),
        description=snippet.get("description"),
        published_at=snippet.get("publishedAt"),
        subscriber_count=_int_or_none(stats.get("subscriberCount")),
        video_count=_int_or_none(stats.get("videoCount")),
        view_count=_int_or_none(stats.get("viewCount")),
        thumbnail_url=_best_thumbnail(snippet.get("thumbnails")),
        uploads_playlist_id=related.get("uploads"),
    )
