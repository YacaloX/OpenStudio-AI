"""Pruebas de almacenamiento de investigación y cálculos de tendencias."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from research.store import ResearchStore
from research.trends import (
    age_bucket,
    analyze_video,
    engagement_rate,
    parse_datetime,
    rank_videos,
    summarize_collection,
    video_age_days,
    views_per_day,
)
from youtube.models import Channel, Video

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def make_video(
    video_id: str = "vid1",
    *,
    published_at: str | None = "2026-05-02T00:00:00Z",  # 30 días antes de NOW
    views: int | None = 3000,
    likes: int | None = 100,
    comments: int | None = 20,
    duration: int | None = 600,
    channel_id: str | None = "UC1",
) -> Video:
    return Video(
        video_id=video_id,
        title=f"Vídeo {video_id}",
        channel_id=channel_id,
        channel_title="Canal",
        published_at=published_at,
        duration_seconds=duration,
        view_count=views,
        like_count=likes,
        comment_count=comments,
    )


# ------------------------------------------------------------------ store
def test_video_upsert_updates_metrics(research: ResearchStore):
    research.upsert_videos([make_video(views=100)])

    research.upsert_videos([make_video(views=500)], source_query="consulta")

    stored = research.video("vid1")
    assert stored is not None
    assert stored.view_count == 500
    assert stored.source_query == "consulta"
    assert research.stats()["videos"] == 1


def test_video_upsert_keeps_previous_source_when_missing(research: ResearchStore):
    research.upsert_videos([make_video()], source_query="primera")
    research.upsert_videos([make_video()])
    stored = research.video("vid1")
    assert stored is not None
    assert stored.source_query == "primera"


def test_channel_upsert_and_listing(research: ResearchStore):
    channel = Channel(channel_id="UC1", title="Canal uno", subscriber_count=1000)
    research.upsert_channels([channel])
    research.upsert_channels([Channel(channel_id="UC2", title="Canal dos")])
    assert research.stats()["channels"] == 2
    channels = research.channels()
    assert {c.channel_id for c in channels} == {"UC1", "UC2"}


def test_queries_are_logged(research: ResearchStore):
    research.log_query("minecraft", "search.list", {"order": "date"}, 10)
    queries = research.recent_queries()
    assert queries[0]["query"] == "minecraft"
    assert queries[0]["result_count"] == 10
    assert research.stats()["queries"] == 1


def test_videos_filtered_by_channel(research: ResearchStore):
    research.upsert_videos([make_video("v1", channel_id="UC1"), make_video("v2", channel_id="UC2")])
    only_uc1 = research.videos(channel_id="UC1")
    assert [v.video_id for v in only_uc1] == ["v1"]
    assert len(research.videos()) == 2


def test_missing_video_returns_none(research: ResearchStore):
    assert research.video("no-existe") is None


# ----------------------------------------------------------------- trends
def test_parse_datetime_accepts_z_and_naive():
    assert parse_datetime("2026-05-02T00:00:00Z").tzinfo is not None  # type: ignore[union-attr]
    parsed = parse_datetime("2026-05-02T00:00:00")
    assert parsed is not None
    assert parsed.tzinfo == UTC
    assert parse_datetime("basura") is None
    assert parse_datetime(None) is None


def test_age_and_views_per_day():
    video = make_video()
    assert video_age_days(video, NOW) == pytest.approx(30.0)
    assert views_per_day(video, NOW) == pytest.approx(100.0)
    assert age_bucket(video_age_days(video, NOW)) == "maduro_>=30d"


def test_views_per_day_requires_publication_date_or_views():
    assert views_per_day(make_video(published_at=None), NOW) is None
    assert views_per_day(make_video(views=None), NOW) is None


def test_age_buckets():
    assert age_bucket(None) == "desconocida"
    assert age_bucket(0.5) == "muy_nuevo_<24h"
    assert age_bucket(3) == "nuevo_<7d"
    assert age_bucket(10) == "reciente_<30d"
    assert age_bucket(90) == "maduro_>=30d"


def test_engagement_rate():
    assert engagement_rate(make_video()) == pytest.approx(0.04)
    assert engagement_rate(make_video(views=0)) is None
    assert engagement_rate(make_video(views=None)) is None
    assert engagement_rate(make_video(likes=None, comments=None)) is None
    assert engagement_rate(make_video(likes=None, comments=10)) == pytest.approx(0.0033, abs=1e-4)


def test_analyze_video_separates_observed_and_derived():
    analysis = analyze_video(make_video(), NOW)
    assert analysis["observed"]["view_count"] == 3000
    assert analysis["derived"]["historical_views_per_day"] == pytest.approx(100.0)
    assert analysis["derived"]["age_days"] == pytest.approx(30.0)
    assert analysis["derived"]["age_bucket"] == "maduro_>=30d"
    assert any("promedio histórico" in note for note in analysis["method_notes"])
    assert analysis["warnings"] == []


def test_analyze_video_warns_about_fresh_or_incomplete_data():
    analysis = analyze_video(make_video(published_at="2026-05-31T18:00:00Z", likes=None), NOW)
    assert any("menos de 24 h" in warning for warning in analysis["warnings"])
    assert any("Likes" in warning for warning in analysis["warnings"])


def test_rank_videos_excludes_too_new_and_incomplete():
    videos = [
        make_video("viejo", views=3000),  # 100/día
        make_video("viejo2", published_at="2026-04-02T00:00:00Z", views=6000),  # 100/día
        make_video("nuevo", published_at="2026-05-31T18:00:00Z", views=50),  # <1 día
        make_video("sin-datos", views=None),
    ]
    ranking = rank_videos(videos, now=NOW, limit=5)
    ids = [entry["video_id"] for entry in ranking["ranking"]]
    assert "nuevo" not in ids
    assert "sin-datos" not in ids
    assert set(ids) == {"viejo", "viejo2"}
    assert ranking["excluded_for_insufficient_age_or_data"] == 2
    assert ranking["sample_size"] == 2
    assert ranking["confidence"] == "baja"
    assert "No implica causalidad" in ranking["note"]


def test_rank_videos_respects_limit_and_order():
    videos = [
        make_video("bajo", views=300),
        make_video("alto", views=9000),
        make_video("medio", views=3000),
    ]
    ranking = rank_videos(videos, now=NOW, limit=2)
    assert [entry["video_id"] for entry in ranking["ranking"]] == ["alto", "medio"]


def test_summarize_collection_reports_small_sample():
    summary = summarize_collection([make_video()], now=NOW)
    assert summary["sample_size"] == 1
    assert summary["confidence"] == "baja"
    assert any("insuficiente" in warning for warning in summary["warnings"])
    assert summary["derived_medians"]["historical_views_per_day"] == pytest.approx(100.0)


def test_summarize_collection_confidence_with_enough_sample():
    videos = [make_video(f"v{i}") for i in range(5)]
    summary = summarize_collection(videos, now=NOW)
    assert summary["confidence"] == "media"
    assert summary["warnings"] == []
