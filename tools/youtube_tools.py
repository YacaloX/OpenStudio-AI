"""Herramientas de YouTube Data API v3 expuestas al modelo.

Todas son de sólo lectura. La búsqueda cuesta 100 unidades de cuota; el resto,
1 unidad por llamada. Los resultados se guardan en SQLite para no repetir
consultas y para poder analizarlos después sin gastar cuota.
"""

from __future__ import annotations

from typing import Any

from research.store import ResearchStore
from research.trends import analyze_video, rank_videos, summarize_collection
from tools.registry import ToolRegistry, ToolSpec
from youtube.client import YouTubeClient


def register_youtube_tools(
    registry: ToolRegistry, client: YouTubeClient, store: ResearchStore
) -> None:
    """Registra las herramientas de YouTube en el registro existente."""

    registry.register(
        ToolSpec(
            name="youtube_search_videos",
            description=(
                "Busca vídeos públicos en YouTube y devuelve sus datos observados. "
                "Cuesta 100 unidades de cuota por página. Úsala para descubrir competencia y "
                "tendencias reales; no inventes métricas privadas (CTR, retención, ingresos) "
                "porque no están disponibles para canales ajenos."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Términos de búsqueda."},
                    "max_results": {
                        "type": "integer",
                        "description": "Vídeos a devolver (1-25).",
                        "default": 10,
                    },
                    "order": {
                        "type": "string",
                        "enum": ["relevance", "date", "viewCount", "rating", "title"],
                        "description": "Criterio de orden de la API.",
                        "default": "relevance",
                    },
                    "published_after_days": {
                        "type": "integer",
                        "description": "Sólo vídeos publicados en los últimos N días.",
                    },
                },
                "required": ["query"],
            },
            category="youtube",
            handler=lambda args: _search(client, store, args),
        )
    )

    registry.register(
        ToolSpec(
            name="youtube_video_details",
            description=(
                "Obtiene los detalles públicos de un vídeo por su id (11 caracteres). "
                "Cuesta 1 unidad de cuota."
            ),
            parameters={
                "type": "object",
                "properties": {"video_id": {"type": "string", "description": "Id del vídeo."}},
                "required": ["video_id"],
            },
            category="youtube",
            handler=lambda args: _video_details(client, store, args),
        )
    )

    registry.register(
        ToolSpec(
            name="youtube_channel_details",
            description=(
                "Obtiene datos públicos de un canal por id (UC...) o handle (@nombre). "
                "Cuesta 1 unidad de cuota."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Id del canal o handle con @."}
                },
                "required": ["channel"],
            },
            category="youtube",
            handler=lambda args: _channel_details(client, store, args),
        )
    )

    registry.register(
        ToolSpec(
            name="youtube_channel_videos",
            description=(
                "Lista los últimos vídeos subidos por un canal con sus estadísticas públicas. "
                "Cuesta 1 unidad por el canal y 1 por cada bloque de 50 vídeos."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Id del canal o handle con @."},
                    "max_results": {
                        "type": "integer",
                        "description": "Vídeos a devolver (1-50).",
                        "default": 20,
                    },
                },
                "required": ["channel"],
            },
            category="youtube",
            handler=lambda args: _channel_videos(client, store, args),
        )
    )

    registry.register(
        ToolSpec(
            name="youtube_compare_videos",
            description=(
                "Compara vídeos ya recopilados usando su promedio histórico de visualizaciones "
                "por día y su engagement público. No es una tasa de crecimiento ni una predicción."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "video_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ids de vídeos (2-20). Si falta alguno localmente se consulta la API.",
                    },
                    "limit": {"type": "integer", "description": "Máximo en el ranking.", "default": 10},
                },
                "required": ["video_ids"],
            },
            category="youtube",
            handler=lambda args: _compare(client, store, args),
        )
    )

    registry.register(
        ToolSpec(
            name="youtube_quota_status",
            description=(
                "Muestra el consumo estimado de cuota de YouTube de hoy y el límite configurado. "
                "No gasta cuota."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            category="youtube",
            handler=lambda args: client.quota_snapshot(),
        )
    )


# ------------------------------------------------------------------ helpers
def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compact(analysis: dict[str, Any]) -> dict[str, Any]:
    """Versión reducida para el prompt (el detalle completo infla el contexto)."""
    observed = analysis["observed"]
    derived = analysis["derived"]
    return {
        "video_id": analysis["video_id"],
        "title": analysis["title"],
        "channel": analysis["channel_title"],
        "url": analysis["url"],
        "published_at": observed["published_at"],
        "views": observed["view_count"],
        "likes": observed["like_count"],
        "comments": observed["comment_count"],
        "duration_s": observed["duration_seconds"],
        "age_days": derived["age_days"],
        "views_per_day": derived["historical_views_per_day"],
        "engagement": derived["engagement_rate"],
        "warnings": analysis["warnings"],
    }


_METHOD_NOTES = [
    "views_per_day = visualizaciones totales / días desde la publicación"
    " (promedio histórico, no tasa actual ni predicción).",
    "engagement = (likes + comentarios) / visualizaciones (sólo datos públicos).",
    "No existen CTR, impresiones, retención ni ingresos de canales ajenos.",
]


def _search(client: YouTubeClient, store: ResearchStore, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    max_results = max(1, min(_as_int(args.get("max_results"), 10), 25))
    order = str(args.get("order", "relevance"))
    published_after_days = args.get("published_after_days")
    days = _as_int(published_after_days, 0) or None

    videos = client.search_videos(
        query, max_results=max_results, order=order, published_after_days=days
    )
    store.upsert_videos(videos, source_query=query)
    store.log_query(
        query,
        "search.list",
        {"order": order, "published_after_days": days, "max_results": max_results},
        len(videos),
    )
    snapshot = client.quota_snapshot()
    return {
        "query": query,
        "count": len(videos),
        "results": [_compact(analyze_video(video)) for video in videos],
        "method_notes": _METHOD_NOTES,
        "quota_used_today": snapshot["used"],
        "quota_remaining": snapshot["remaining"],
        "reminder": (
            "Datos públicos observados. No hay CTR, retención ni ingresos de canales ajenos. "
            "El promedio de visualizaciones/día no es una tasa de crecimiento actual."
        ),
    }


def _video_details(client: YouTubeClient, store: ResearchStore, args: dict[str, Any]) -> dict[str, Any]:
    video_id = str(args.get("video_id", "")).strip()
    videos = client.get_videos([video_id])
    if not videos:
        return {"found": False, "video_id": video_id, "note": "Vídeo no encontrado o privado."}
    store.upsert_videos(videos)
    return {
        "found": True,
        "video": _compact(analyze_video(videos[0])),
        "method_notes": _METHOD_NOTES,
    }


def _channel_details(client: YouTubeClient, store: ResearchStore, args: dict[str, Any]) -> dict[str, Any]:
    reference = str(args.get("channel", "")).strip()
    channel = (
        client.get_channel(handle=reference) if reference.startswith("@") else client.get_channel(reference)
    )
    store.upsert_channels([channel])
    return {"channel": channel.to_dict()}


def _channel_videos(client: YouTubeClient, store: ResearchStore, args: dict[str, Any]) -> dict[str, Any]:
    reference = str(args.get("channel", "")).strip()
    max_results = max(1, min(_as_int(args.get("max_results"), 20), 50))
    videos = client.get_channel_videos(reference, max_results=max_results)
    store.upsert_videos(videos)
    store.log_query(reference, "playlistItems.list", {"max_results": max_results}, len(videos))
    return {
        "channel": reference,
        "count": len(videos),
        "summary": summarize_collection(videos),
        "videos": [_compact(analyze_video(video)) for video in videos],
        "method_notes": _METHOD_NOTES,
    }


def _compare(client: YouTubeClient, store: ResearchStore, args: dict[str, Any]) -> dict[str, Any]:
    raw_ids = args.get("video_ids") or []
    if not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    ids = [str(value).strip() for value in raw_ids if str(value).strip()][:20]
    if len(ids) < 2:
        return {"error": "Se necesitan al menos 2 video_ids para comparar."}

    found = [video for vid in ids if (video := store.video(vid)) is not None]
    found_ids = {video.video_id for video in found}
    missing = [vid for vid in ids if vid not in found_ids]
    if missing:
        fetched = client.get_videos(missing)
        found.extend(fetched)
        store.upsert_videos(fetched)

    comparison = rank_videos(found, limit=max(2, min(_as_int(args.get("limit"), 10), 20)))
    return {
        "requested": len(ids),
        "found": len(found),
        "comparison": comparison,
        "per_video": [_compact(analyze_video(video)) for video in found],
        "method_notes": _METHOD_NOTES,
        "reminder": "Correlación no implica causalidad. Muestra pequeña = confianza baja.",
    }
