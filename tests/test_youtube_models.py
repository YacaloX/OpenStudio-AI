"""Pruebas de parsers y modelos de la YouTube Data API."""

from __future__ import annotations

import pytest
from fakes import channel_item, video_item

from youtube.models import channel_from_api, parse_duration, video_from_api


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PT3M34S", 214),
        ("PT1H2M3S", 3723),
        ("P1DT1S", 86401),
        ("PT0S", 0),
        ("PT45S", 45),
        ("P2D", 172800),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", [None, "", "basura", "P1Y2M"])
def test_parse_duration_invalid(value):
    assert parse_duration(value) is None


def test_video_from_api_extracts_all_public_fields():
    video = video_from_api(video_item("abc12345678", title="Título", views="1000", likes="10", comments="2"))
    assert video.video_id == "abc12345678"
    assert video.title == "Título"
    assert video.channel_title == "Canal de prueba"
    assert video.published_at == "2026-01-01T00:00:00Z"
    assert video.duration_seconds == 600
    assert video.view_count == 1000
    assert video.like_count == 10
    assert video.comment_count == 2
    assert video.url == "https://www.youtube.com/watch?v=abc12345678"
    assert video.thumbnail_url is not None
    assert video.thumbnail_url.endswith("hq.jpg")


def test_video_from_api_handles_hidden_counts():
    video = video_from_api(video_item("abc12345678", views=None, likes=None, comments=None))
    assert video.view_count is None
    assert video.like_count is None
    assert video.comment_count is None
    assert video.to_dict()["url"].startswith("https://www.youtube.com/watch")


def test_video_from_api_tolerates_missing_snippet():
    video = video_from_api({"id": "x"})
    assert video.title == "(sin título)"
    assert video.duration_seconds is None
    assert video.view_count is None


def test_channel_from_api():
    channel = channel_from_api(channel_item("UCabc", title="Mi canal", subscribers="5000"))
    assert channel.channel_id == "UCabc"
    assert channel.subscriber_count == 5000
    assert channel.video_count == 100
    assert channel.uploads_playlist_id == "UUabc"
    assert channel.url == "https://www.youtube.com/channel/UCabc"
    assert channel.to_dict()["title"] == "Mi canal"
