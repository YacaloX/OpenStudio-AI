"""Cálculos de rendimiento público y comparaciones razonables.

Principios:

- Se separa siempre ``observed`` (lo que dice la API) de ``derived`` (lo que
  calculamos) y de las advertencias sobre datos insuficientes.
- ``views_per_day`` es un **promedio histórico** desde la publicación, no una
  tasa de crecimiento actual ni una predicción.
- Nada de esto son métricas privadas: CTR, retención e ingresos no existen
  para canales ajenos.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from youtube.models import Video

MIN_SAMPLE_FOR_CONCLUSIONS = 5


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def video_age_days(video: Video, now: datetime | None = None) -> float | None:
    published = parse_datetime(video.published_at)
    if published is None:
        return None
    reference = now or datetime.now(UTC)
    return max(0.0, (reference - published).total_seconds() / 86400)


def age_bucket(age_days: float | None) -> str:
    if age_days is None:
        return "desconocida"
    if age_days < 1:
        return "muy_nuevo_<24h"
    if age_days < 7:
        return "nuevo_<7d"
    if age_days < 30:
        return "reciente_<30d"
    return "maduro_>=30d"


def views_per_day(video: Video, now: datetime | None = None) -> float | None:
    """Promedio histórico de visualizaciones por día desde la publicación."""
    if video.view_count is None:
        return None
    age = video_age_days(video, now)
    if age is None or age <= 0:
        return None
    return round(video.view_count / age, 1)


def engagement_rate(video: Video) -> float | None:
    """(likes + comentarios) / visualizaciones, si hay datos suficientes."""
    if not video.view_count or video.view_count <= 0:
        return None
    if video.like_count is None and video.comment_count is None:
        return None
    likes = video.like_count or 0
    comments = video.comment_count or 0
    return round((likes + comments) / video.view_count, 4)


def analyze_video(video: Video, now: datetime | None = None) -> dict[str, Any]:
    """Ficha de un vídeo con datos observados, derivados y advertencias."""
    age = video_age_days(video, now)
    per_day = views_per_day(video, now)
    engagement = engagement_rate(video)

    warnings: list[str] = []
    if age is None:
        warnings.append("Fecha de publicación desconocida: no se pueden calcular tasas.")
    elif age < 1:
        warnings.append("Publicado hace menos de 24 h: cualquier tasa es inestable.")
    if video.like_count is None:
        warnings.append("Likes ocultos o no disponibles: el engagement está incompleto.")
    if video.view_count is None:
        warnings.append("Visualizaciones no disponibles.")

    return {
        "video_id": video.video_id,
        "title": video.title,
        "url": video.url,
        "channel_id": video.channel_id,
        "channel_title": video.channel_title,
        "observed": {
            "published_at": video.published_at,
            "view_count": video.view_count,
            "like_count": video.like_count,
            "comment_count": video.comment_count,
            "duration_seconds": video.duration_seconds,
        },
        "derived": {
            "age_days": round(age, 2) if age is not None else None,
            "age_bucket": age_bucket(age),
            "historical_views_per_day": per_day,
            "engagement_rate": engagement,
        },
        "method_notes": [
            "historical_views_per_day = visualizaciones totales / días desde la publicación"
            " (promedio histórico, no tasa actual ni predicción).",
            "engagement_rate = (likes + comentarios) / visualizaciones (sólo datos públicos).",
        ],
        "warnings": warnings,
    }


def rank_videos(
    videos: list[Video],
    *,
    now: datetime | None = None,
    limit: int = 10,
    min_age_days: float = 1.0,
) -> dict[str, Any]:
    """Ordena vídeos por promedio histórico de visualizaciones/día.

    Se excluyen los publicados hace menos de ``min_age_days`` para no comparar
    un vídeo de 2 horas con uno de dos años.
    """
    usable: list[tuple[float, Video]] = []
    excluded = 0
    for video in videos:
        age = video_age_days(video, now)
        rate = views_per_day(video, now)
        if rate is None or age is None or age < min_age_days:
            excluded += 1
            continue
        usable.append((rate, video))

    usable.sort(key=lambda pair: pair[0], reverse=True)
    ranked = []
    for rate, video in usable[: max(1, int(limit))]:
        ranked.append(
            {
                "video_id": video.video_id,
                "title": video.title,
                "url": video.url,
                "channel_title": video.channel_title,
                "observed_views": video.view_count,
                "age_days": round(video_age_days(video, now) or 0.0, 2),
                "historical_views_per_day": rate,
                "engagement_rate": engagement_rate(video),
            }
        )

    return {
        "ranking": ranked,
        "sample_size": len(usable),
        "excluded_for_insufficient_age_or_data": excluded,
        "min_age_days_applied": min_age_days,
        "note": (
            "Comparación de promedios históricos por día. No implica causalidad ni "
            "predice el rendimiento de un vídeo nuevo."
        ),
        "confidence": "baja" if len(usable) < MIN_SAMPLE_FOR_CONCLUSIONS else "media",
    }


def summarize_collection(videos: list[Video], *, now: datetime | None = None) -> dict[str, Any]:
    """Resumen descriptivo de una muestra de vídeos, con aviso de muestra pequeña."""
    rates = [rate for video in videos if (rate := views_per_day(video, now)) is not None]
    engagements = [
        value for video in videos if (value := engagement_rate(video)) is not None
    ]
    durations = [
        video.duration_seconds for video in videos if video.duration_seconds is not None
    ]
    buckets: dict[str, int] = {}
    for video in videos:
        bucket = age_bucket(video_age_days(video, now))
        buckets[bucket] = buckets.get(bucket, 0) + 1

    result: dict[str, Any] = {
        "sample_size": len(videos),
        "age_buckets": buckets,
        "observed_totals": {
            "views": sum(v.view_count or 0 for v in videos),
            "likes": sum(v.like_count or 0 for v in videos),
            "comments": sum(v.comment_count or 0 for v in videos),
        },
        "derived_medians": {
            "historical_views_per_day": round(statistics.median(rates), 1) if rates else None,
            "engagement_rate": round(statistics.median(engagements), 4) if engagements else None,
            "duration_seconds": round(statistics.median(durations)) if durations else None,
        },
        "warnings": [],
        "confidence": "media" if len(videos) >= MIN_SAMPLE_FOR_CONCLUSIONS else "baja",
    }
    if len(videos) < MIN_SAMPLE_FOR_CONCLUSIONS:
        result["warnings"].append(
            f"Muestra de {len(videos)} vídeos (< {MIN_SAMPLE_FOR_CONCLUSIONS}): insuficiente para "
            "conclusiones; úsala sólo como indicio."
        )
    if len(buckets) > 1:
        result["warnings"].append(
            "Hay vídeos de edades muy distintas; compara siempre dentro de franjas de antigüedad."
        )
    return result
