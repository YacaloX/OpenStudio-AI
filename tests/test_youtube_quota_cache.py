"""Pruebas de cuota y caché de YouTube."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.errors import YouTubeQuotaError
from memory.db import Database, utc_now
from youtube.cache import HttpCache, cache_key
from youtube.quota import QuotaManager, utc_day


def test_quota_charge_accumulates():
    db = Database(":memory:")
    quota = QuotaManager(db, daily_limit=1000)
    assert quota.remaining() == 1000
    quota.charge("videos")
    quota.charge("videos")
    quota.charge("search")
    assert quota.used() == 102
    assert quota.remaining() == 898
    snap = quota.snapshot()
    by_endpoint = snap["by_endpoint"]
    assert isinstance(by_endpoint, dict)
    assert by_endpoint["videos"] == {"units": 2, "calls": 2}
    assert by_endpoint["search"] == {"units": 100, "calls": 1}
    db.close()


def test_quota_rejects_call_over_limit():
    db = Database(":memory:")
    quota = QuotaManager(db, daily_limit=100)
    quota.charge("videos")
    with pytest.raises(YouTubeQuotaError):
        quota.charge("search")
    assert quota.used() == 1
    db.close()


def test_quota_is_per_utc_day():
    db = Database(":memory:")
    quota = QuotaManager(db, daily_limit=1000)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO api_quota(day, endpoint, units, calls) VALUES(?,?,?,?)",
        (yesterday, "search", 500, 5),
    )
    assert quota.used() == 0
    assert quota.used(yesterday) == 500
    assert utc_day() != yesterday
    db.close()


def test_unknown_endpoint_costs_one_unit():
    db = Database(":memory:")
    quota = QuotaManager(db, daily_limit=10)
    assert quota.cost_of("canales") == 1
    db.close()


def test_cache_set_and_get():
    db = Database(":memory:")
    cache = HttpCache(db)
    cache.set("k1", "videos", {"items": [1, 2]}, ttl_seconds=60)
    assert cache.get("k1") == {"items": [1, 2]}
    assert cache.get("no-existe") is None
    db.close()


def test_cache_expires_and_is_deleted():
    db = Database(":memory:")
    cache = HttpCache(db)
    cache.set("k1", "videos", {"items": []}, ttl_seconds=60)
    expired = (datetime.now(UTC) - timedelta(seconds=5)).isoformat(timespec="seconds")
    db.execute("UPDATE http_cache SET expires_at=? WHERE cache_key='k1'", (expired,))
    assert cache.get("k1") is None
    assert db.query("SELECT COUNT(*) AS n FROM http_cache")[0]["n"] == 0
    db.close()


def test_cache_ttl_zero_does_not_store():
    db = Database(":memory:")
    cache = HttpCache(db)
    cache.set("k1", "videos", {"items": []}, ttl_seconds=0)
    assert cache.get("k1") is None
    db.close()


def test_cache_purge_expired():
    db = Database(":memory:")
    cache = HttpCache(db)
    cache.set("old", "videos", {}, ttl_seconds=60)
    cache.set("new", "videos", {}, ttl_seconds=3600)
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
    db.execute("UPDATE http_cache SET expires_at=? WHERE cache_key='old'", (past,))
    assert cache.purge_expired() == 1
    assert cache.get("new") == {}
    db.close()


def test_cache_key_depends_on_params_and_ignores_key():
    assert cache_key("search", {"q": "a", "key": "1"}) == cache_key("search", {"q": "a", "key": "2"})
    assert cache_key("search", {"q": "a"}) != cache_key("search", {"q": "b"})
    assert cache_key("search", {"q": "a"}) != cache_key("videos", {"q": "a"})
    assert len(cache_key("search", {})) == 64


def test_cache_corrupt_payload_is_discarded():
    db = Database(":memory:")
    cache = HttpCache(db)
    db.execute(
        "INSERT INTO http_cache(cache_key, endpoint, payload, created_at, expires_at) VALUES(?,?,?,?,?)",
        ("bad", "videos", "{no-json", utc_now(), "2999-01-01T00:00:00+00:00"),
    )
    assert cache.get("bad") is None
    db.close()
