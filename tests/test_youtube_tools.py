"""Pruebas de las herramientas de YouTube expuestas al modelo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.errors import YouTubeQuotaError
from models.base import ToolCall
from tools import build_default_registry
from youtube.models import Channel, Video

NOW = datetime.now(UTC)
THIRTY_DAYS_AGO = (NOW - timedelta(days=30)).isoformat(timespec="seconds")


def make_video(video_id: str, *, views: int = 3000, published: str | None = None) -> Video:
    return Video(
        video_id=video_id,
        title=f"Vídeo {video_id}",
        channel_id="UC1",
        channel_title="Canal",
        published_at=published or THIRTY_DAYS_AGO,
        duration_seconds=600,
        view_count=views,
        like_count=100,
        comment_count=20,
    )


class StubYouTube:
    def __init__(self, videos=None, channel=None, error: Exception | None = None) -> None:
        self.videos = list(videos or [])
        self.channel = channel or Channel(channel_id="UC1", title="Canal uno", uploads_playlist_id="UU1")
        self.error = error
        self.search_calls: list[dict] = []
        self.video_calls: list[list[str]] = []
        self.channel_calls: list[dict] = []
        self.channel_videos_calls: list[dict] = []

    def _check(self):
        if self.error:
            raise self.error

    def search_videos(self, query, **kwargs):
        self._check()
        self.search_calls.append({"query": query, **kwargs})
        return self.videos

    def get_videos(self, ids):
        self._check()
        self.video_calls.append(list(ids))
        return [video for video in self.videos if video.video_id in ids]

    def get_channel(self, channel_id=None, *, handle=None):
        self._check()
        self.channel_calls.append({"channel_id": channel_id, "handle": handle})
        return self.channel

    def get_channel_videos(self, reference, *, max_results=20):
        self._check()
        self.channel_videos_calls.append({"reference": reference, "max_results": max_results})
        return self.videos

    def quota_snapshot(self):
        return {"day_utc": "2026-06-01", "daily_limit": 10000, "used": 101, "remaining": 9899,
                "by_endpoint": {"search": {"units": 100, "calls": 1}}, "note": "local"}


def registry_with(store, settings, research, stub: StubYouTube):
    return build_default_registry(store, settings, youtube_client=stub, research_store=research)


def call(name: str, arguments: dict | None = None) -> ToolCall:
    return ToolCall(id="call", name=name, arguments=arguments or {})


def test_youtube_tools_registered_with_client(store, settings, research):
    registry = registry_with(store, settings, research, StubYouTube())
    assert len(registry) == 12
    for name in (
        "youtube_search_videos",
        "youtube_video_details",
        "youtube_channel_details",
        "youtube_channel_videos",
        "youtube_compare_videos",
        "youtube_quota_status",
    ):
        assert name in registry.names()
    assert all(spec["type"] == "function" for spec in registry.schemas())


def test_search_stores_results_and_logs_query(store, settings, research):
    stub = StubYouTube(videos=[make_video("v1"), make_video("v2", views=6000)])
    registry = registry_with(store, settings, research, stub)

    result = registry.execute(call("youtube_search_videos", {"query": "tema", "max_results": 2}))

    assert result.ok
    assert result.data["count"] == 2
    assert result.data["results"][0]["views_per_day"] == pytest.approx(100.0)
    assert stub.search_calls[0]["query"] == "tema"
    assert stub.search_calls[0]["max_results"] == 2
    assert research.stats()["videos"] == 2
    assert research.stats()["queries"] == 1
    assert "CTR" in result.data["reminder"]


def test_search_clamps_max_results(store, settings, research):
    stub = StubYouTube(videos=[])
    registry = registry_with(store, settings, research, stub)
    registry.execute(call("youtube_search_videos", {"query": "x", "max_results": 999}))
    assert stub.search_calls[0]["max_results"] == 25


def test_video_details_found_and_not_found(store, settings, research):
    stub = StubYouTube(videos=[make_video("v1")])
    registry = registry_with(store, settings, research, stub)

    found = registry.execute(call("youtube_video_details", {"video_id": "v1"}))
    assert found.ok
    assert found.data["found"] is True
    assert found.data["video"]["video_id"] == "v1"

    missing = registry.execute(call("youtube_video_details", {"video_id": "nope"}))
    assert missing.ok
    assert missing.data["found"] is False


def test_channel_details_by_handle(store, settings, research):
    stub = StubYouTube()
    registry = registry_with(store, settings, research, stub)
    result = registry.execute(call("youtube_channel_details", {"channel": "@yacalox"}))
    assert result.ok
    assert stub.channel_calls[0]["handle"] == "@yacalox"
    assert result.data["channel"]["channel_id"] == "UC1"
    assert research.stats()["channels"] == 1


def test_channel_videos_returns_summary(store, settings, research):
    stub = StubYouTube(videos=[make_video("v1")])
    registry = registry_with(store, settings, research, stub)
    result = registry.execute(call("youtube_channel_videos", {"channel": "UC1", "max_results": 5}))
    assert result.ok
    assert result.data["count"] == 1
    assert result.data["summary"]["sample_size"] == 1
    assert stub.channel_videos_calls[0]["max_results"] == 5


def test_compare_uses_local_data_without_api(store, settings, research):
    research.upsert_videos([make_video("v1"), make_video("v2", views=9000)])
    stub = StubYouTube()
    registry = registry_with(store, settings, research, stub)

    result = registry.execute(call("youtube_compare_videos", {"video_ids": ["v1", "v2"]}))

    assert result.ok
    assert result.data["found"] == 2
    assert result.data["comparison"]["ranking"][0]["video_id"] == "v2"
    assert stub.video_calls == []


def test_compare_fetches_missing_videos(store, settings, research):
    research.upsert_videos([make_video("v1")])
    stub = StubYouTube(videos=[make_video("v2")])
    registry = registry_with(store, settings, research, stub)

    result = registry.execute(call("youtube_compare_videos", {"video_ids": ["v1", "v2"]}))

    assert result.ok
    assert stub.video_calls == [["v2"]]
    assert research.stats()["videos"] == 2


def test_compare_requires_two_ids(store, settings, research):
    registry = registry_with(store, settings, research, StubYouTube())
    result = registry.execute(call("youtube_compare_videos", {"video_ids": ["v1"]}))
    assert result.ok
    assert "error" in result.data


def test_quota_status_does_not_need_network(store, settings, research):
    registry = registry_with(store, settings, research, StubYouTube())
    result = registry.execute(call("youtube_quota_status"))
    assert result.ok
    assert result.data["remaining"] == 9899


def test_quota_error_is_returned_to_model(store, settings, research):
    stub = StubYouTube(error=YouTubeQuotaError("sin cuota"))
    registry = registry_with(store, settings, research, stub)
    result = registry.execute(call("youtube_search_videos", {"query": "x"}))
    assert not result.ok
    assert "cuota" in result.error.lower()
    assert store.tool_calls()[0]["status"] == "error"
