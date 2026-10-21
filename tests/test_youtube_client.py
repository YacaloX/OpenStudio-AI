"""Pruebas del cliente de YouTube Data API (sin red)."""

from __future__ import annotations

from typing import cast

import pytest
import requests
from fakes import FakeResponse, FakeSession, channel_item, endpoint_of, search_item, video_item

from core.errors import YouTubeAPIError, YouTubeConfigError, YouTubeError, YouTubeQuotaError
from memory.db import Database
from youtube.client import YouTubeClient


def make_client(db, handler, **kwargs) -> tuple[YouTubeClient, FakeSession]:
    session = FakeSession(handler)
    client = YouTubeClient(
        "clave-de-prueba",
        db,
        daily_quota=kwargs.pop("daily_quota", 10000),
        cache_ttl=kwargs.pop("cache_ttl", 3600),
        session=cast(requests.Session, session),
        sleep=lambda seconds: None,
        **kwargs,
    )
    return client, session


def search_handler(search_pages: list[dict], videos: list[dict]):
    def handler(url: str, params: dict) -> FakeResponse:
        endpoint = endpoint_of(url)
        if endpoint == "search":
            token = params.get("pageToken")
            index = int(token.split(":")[1]) if token else 0
            return FakeResponse(payload=search_pages[index])
        if endpoint == "videos":
            ids = set(params.get("id", "").split(","))
            return FakeResponse(payload={"items": [v for v in videos if v["id"] in ids]})
        raise AssertionError(f"endpoint inesperado: {endpoint}")

    return handler


def test_requires_api_key():
    db = Database(":memory:")
    with pytest.raises(YouTubeConfigError):
        YouTubeClient("   ", db)
    db.close()


def test_search_parses_and_charges_quota():
    db = Database(":memory:")
    pages = [{"items": [search_item("vid00000001"), search_item("vid00000002")]}]
    videos = [video_item("vid00000001"), video_item("vid00000002", title="Otro")]
    client, session = make_client(db, search_handler(pages, videos))

    result = client.search_videos("minecraft", max_results=2)

    assert [video.video_id for video in result] == ["vid00000001", "vid00000002"]
    assert result[0].view_count == 10000
    assert result[0].duration_seconds == 600
    search_call = next(call for call in session.calls if endpoint_of(call["url"]) == "search")
    assert search_call["params"]["q"] == "minecraft"
    assert search_call["params"]["type"] == "video"
    assert search_call["params"]["regionCode"] == "CO"
    assert search_call["params"]["relevanceLanguage"] == "es"
    assert search_call["params"]["key"] == "clave-de-prueba"
    assert client.quota.used() == 101  # search 100 + videos 1
    db.close()


def test_search_sends_published_after():
    db = Database(":memory:")
    client, session = make_client(db, search_handler([{"items": []}], []))
    client.search_videos("tema", published_after_days=7)
    search_call = next(call for call in session.calls if endpoint_of(call["url"]) == "search")
    assert search_call["params"]["publishedAfter"].endswith("Z")
    db.close()


def test_pagination_stops_at_max_results():
    db = Database(":memory:")
    pages: list[dict] = [
        {"items": [search_item("vid00000001"), search_item("vid00000002")], "nextPageToken": "page:1"},
        {"items": [search_item("vid00000003")]},
    ]
    videos = [video_item(f"vid0000000{i}") for i in range(1, 4)]
    client, session = make_client(db, search_handler(pages, videos))

    result = client.search_videos("tema", max_results=3)

    assert len(result) == 3
    assert sum(1 for call in session.calls if endpoint_of(call["url"]) == "search") == 2
    assert client.quota.used() == 201  # dos páginas de búsqueda + una de vídeos
    db.close()


def test_cache_avoids_second_quota_charge():
    db = Database(":memory:")
    pages = [{"items": [search_item("vid00000001")]}]
    videos = [video_item("vid00000001")]
    client, session = make_client(db, search_handler(pages, videos))

    client.search_videos("igual", max_results=1)
    used_after_first = client.quota.used()
    client.search_videos("igual", max_results=1)

    assert client.quota.used() == used_after_first == 101
    assert len(session.calls) == 2  # sólo las llamadas originales
    db.close()


def test_quota_exceeded_from_google():
    db = Database(":memory:")

    def handler(url, params):
        return FakeResponse(
            status_code=403,
            payload={
                "error": {
                    "message": "The request cannot be completed because you have exceeded your quota.",
                    "errors": [{"reason": "quotaExceeded"}],
                }
            },
        )

    client, _ = make_client(db, handler)
    with pytest.raises(YouTubeQuotaError):
        client.search_videos("tema")
    db.close()


def test_invalid_key_maps_to_config_error():
    db = Database(":memory:")

    def handler(url, params):
        return FakeResponse(
            status_code=400,
            payload={
                "error": {
                    "message": "API key not valid. Please pass a valid API key.",
                    "errors": [{"reason": "badRequest"}],
                }
            },
        )

    client, _ = make_client(db, handler)
    with pytest.raises(YouTubeConfigError):
        client.get_videos(["vid00000001"])
    db.close()


def test_retry_on_429_then_success():
    db = Database(":memory:")
    responses = [
        FakeResponse(status_code=429, payload={"error": {"message": "rate limited"}}),
        FakeResponse(payload={"items": [video_item("vid00000001")]}),
    ]
    sleeps: list[float] = []

    def handler(url, params):
        return responses.pop(0)

    client, session = make_client(db, handler, max_retries=1)
    client._sleep = sleeps.append

    videos = client.get_videos(["vid00000001"])

    assert len(videos) == 1
    assert sleeps == [1]
    assert len(session.calls) == 2
    assert client.quota.used() == 2  # se cobra cada intento
    db.close()


def test_network_error_after_retries():
    db = Database(":memory:")

    def handler(url, params):
        raise requests.ConnectionError("sin red")

    client, _ = make_client(db, handler, max_retries=1)
    with pytest.raises(YouTubeAPIError):
        client.get_videos(["vid00000001"])
    db.close()


def test_local_quota_limit_blocks_call():
    db = Database(":memory:")
    client, session = make_client(db, search_handler([{"items": []}], []), daily_quota=50)
    with pytest.raises(YouTubeQuotaError):
        client.search_videos("tema")
    assert session.calls == []
    db.close()


def test_get_channel_by_id_and_handle():
    db = Database(":memory:")

    def handler(url, params):
        assert endpoint_of(url) == "channels"
        if "forHandle" in params:
            assert params["forHandle"] == "yacalox"
        return FakeResponse(payload={"items": [channel_item(title="YacaloX")]})

    client, _ = make_client(db, handler)
    assert client.get_channel("UCchannel0001").title == "YacaloX"
    assert client.get_channel(handle="@yacalox").title == "YacaloX"
    assert client.get_channel(handle="@yacalox").uploads_playlist_id == "UUchannel0001"
    db.close()


def test_get_channel_not_found():
    db = Database(":memory:")
    client, _ = make_client(db, lambda url, params: FakeResponse(payload={"items": []}))
    with pytest.raises(YouTubeError):
        client.get_channel("UCnoexiste")
    db.close()


def test_channel_videos_flow():
    db = Database(":memory:")

    def handler(url, params):
        endpoint = endpoint_of(url)
        if endpoint == "channels":
            return FakeResponse(payload={"items": [channel_item()]})
        if endpoint == "playlistItems":
            return FakeResponse(
                payload={
                    "items": [
                        {"contentDetails": {"videoId": "vid00000001"}},
                        {"contentDetails": {"videoId": "vid00000002"}},
                    ]
                }
            )
        if endpoint == "videos":
            return FakeResponse(
                payload={"items": [video_item("vid00000001"), video_item("vid00000002")]}
            )
        raise AssertionError(endpoint)

    client, session = make_client(db, handler)
    videos = client.get_channel_videos("UCchannel0001", max_results=10)

    assert len(videos) == 2
    assert session.endpoints() == ["channels", "playlistItems", "videos"]
    assert client.quota.used() == 3
    db.close()


def test_channel_videos_accepts_handle():
    db = Database(":memory:")
    seen: list[str] = []

    def handler(url, params):
        endpoint = endpoint_of(url)
        seen.append(endpoint)
        if endpoint == "channels":
            return FakeResponse(payload={"items": [channel_item()]})
        if endpoint == "playlistItems":
            return FakeResponse(payload={"items": [{"contentDetails": {"videoId": "vid00000001"}}]})
        return FakeResponse(payload={"items": [video_item("vid00000001")]})

    client, _ = make_client(db, handler)
    assert len(client.get_channel_videos("@canal")) == 1
    assert seen[0] == "channels"
    db.close()


def test_close_closes_session():
    db = Database(":memory:")
    client, session = make_client(db, search_handler([{"items": []}], []))
    client.close()
    assert session.closed
    db.close()
