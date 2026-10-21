"""Dobles de prueba para la API de YouTube (sin red)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Handler = Callable[[str, dict[str, Any]], "FakeResponse"]


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or "{}"

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Sesión HTTP simulada que enruta por endpoint de la URL."""

    def __init__(self, handler: Handler) -> None:
        self.headers: dict[str, str] = {}
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> FakeResponse:
        query = dict(params or {})
        self.calls.append({"url": url, "params": query, "timeout": timeout})
        return self.handler(url, query)

    def close(self) -> None:
        self.closed = True

    def endpoints(self) -> list[str]:
        return [call["url"].rsplit("/", 1)[-1] for call in self.calls]


def video_item(
    video_id: str = "vid00000001",
    *,
    title: str = "Vídeo de prueba",
    channel_id: str = "UCchannel0001",
    channel_title: str = "Canal de prueba",
    published_at: str = "2026-01-01T00:00:00Z",
    duration: str = "PT10M0S",
    views: str | None = "10000",
    likes: str | None = "500",
    comments: str | None = "50",
    description: str = "descripción",
) -> dict[str, Any]:
    statistics: dict[str, str] = {}
    if views is not None:
        statistics["viewCount"] = views
    if likes is not None:
        statistics["likeCount"] = likes
    if comments is not None:
        statistics["commentCount"] = comments
    return {
        "id": video_id,
        "snippet": {
            "title": title,
            "channelId": channel_id,
            "channelTitle": channel_title,
            "publishedAt": published_at,
            "description": description,
            "thumbnails": {"high": {"url": f"https://i.ytimg.com/vi/{video_id}/hq.jpg"}},
        },
        "statistics": statistics,
        "contentDetails": {"duration": duration},
    }


def channel_item(
    channel_id: str = "UCchannel0001",
    *,
    title: str = "Canal de prueba",
    subscribers: str = "12345",
    videos: str = "100",
    views: str = "999999",
    uploads: str | None = None,
) -> dict[str, Any]:
    return {
        "id": channel_id,
        "snippet": {
            "title": title,
            "publishedAt": "2020-05-01T00:00:00Z",
            "description": "canal",
            "thumbnails": {"default": {"url": "https://yt3.ggpht.com/avatar.jpg"}},
        },
        "statistics": {"subscriberCount": subscribers, "videoCount": videos, "viewCount": views},
        "contentDetails": {"relatedPlaylists": {"uploads": uploads or f"UU{channel_id[2:]}"}},
    }


def search_item(video_id: str, title: str = "Resultado", channel_id: str = "UCchannel0001") -> dict:
    return {
        "id": {"videoId": video_id},
        "snippet": {
            "title": title,
            "channelId": channel_id,
            "channelTitle": "Canal de prueba",
            "publishedAt": "2026-01-01T00:00:00Z",
        },
    }


def endpoint_of(url: str) -> str:
    return url.rsplit("/", 1)[-1]
