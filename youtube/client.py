"""Cliente de la YouTube Data API v3.

Sólo datos públicos. Reglas de la integración:

- Toda llamada pasa por caché TTL y contabilidad de cuota.
- Paginación controlada (``max_results`` real, no infinito).
- Reintentos con espera exponencial ante 429/5xx y fallos de red.
- Errores mapeados a excepciones del dominio, con mensajes accionables.
- Nunca se registra la API key en logs ni en la base de datos.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from core.errors import YouTubeAPIError, YouTubeConfigError, YouTubeError, YouTubeQuotaError
from memory.db import Database
from youtube.cache import HttpCache, cache_key
from youtube.models import Channel, Video, channel_from_api, video_from_api
from youtube.quota import QuotaManager

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"
MAX_PAGE_SIZE = 50
MAX_PAGES = 10
RETRY_STATUS = {429, 500, 502, 503, 504}


class YouTubeClient:
    """Acceso de sólo lectura a la Data API v3."""

    def __init__(
        self,
        api_key: str,
        db: Database,
        *,
        daily_quota: int = 10000,
        cache_ttl: int = 21600,
        timeout: float = 20.0,
        region: str = "CO",
        language: str = "es",
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
    ) -> None:
        if not api_key or not api_key.strip():
            raise YouTubeConfigError(
                "Falta YOUTUBE_API_KEY. Créala en Google Cloud Console (YouTube Data API v3) "
                "y añádela al archivo .env del proyecto."
            )
        self._api_key = api_key.strip()
        self.db = db
        self.cache = HttpCache(db)
        self.quota = QuotaManager(db, daily_limit=daily_quota)
        self.cache_ttl = int(cache_ttl)
        self.timeout = float(timeout)
        self.region = region
        self.language = language
        self._sleep = sleep
        self.max_retries = max(0, int(max_retries))
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", "YacaloX-AI/0.2 (uso personal)")

    # ------------------------------------------------------------ interno
    def _request_page(self, endpoint: str, params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Una página: caché → cuota → HTTP. Devuelve (payload, desde_cache)."""
        key = cache_key(endpoint, params)
        cached = self.cache.get(key)
        if cached is not None:
            return cached, True

        query = {**params, "key": self._api_key}
        url = f"{API_BASE}/{endpoint}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self.quota.charge(endpoint)
            try:
                response = self._session.get(url, params=query, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                logger.warning("red: %s (intento %d/%d)", type(exc).__name__, attempt + 1, self.max_retries + 1)
                if attempt < self.max_retries:
                    self._sleep(2**attempt)
                    continue
                raise YouTubeAPIError(
                    f"No se pudo contactar con la API de YouTube tras {attempt + 1} intentos: {exc}"
                ) from exc

            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise YouTubeAPIError("La API de YouTube devolvió una respuesta que no es JSON.") from exc
                self.cache.set(key, endpoint, payload, self.cache_ttl)
                return payload, False

            if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                logger.warning("HTTP %s en %s; reintentando", response.status_code, endpoint)
                self._sleep(2**attempt)
                continue

            self._raise_for_error(response)
            raise YouTubeAPIError(f"HTTP {response.status_code} inesperado en {endpoint}.")

        raise YouTubeAPIError(f"Falló la llamada a {endpoint}: {last_error}")

    def _raise_for_error(self, response: requests.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error = payload.get("error") or {}
        reason = ""
        message = str(error.get("message") or response.text[:300])
        for detail in error.get("errors") or []:
            if detail.get("reason"):
                reason = str(detail["reason"])
                break
        lowered = f"{reason} {message}".lower()

        if "quota" in lowered:
            raise YouTubeQuotaError(
                "Google rechazó la llamada por cuota agotada en el proyecto. Comprueba la cuota en "
                "Google Cloud Console y espera al reinicio diario (medianoche PT)."
            )
        if response.status_code in (401, 403) or "keyinvalid" in lowered or "api key not valid" in lowered:
            raise YouTubeConfigError(
                "La API key de YouTube parece inválida, restringida o sin permisos para YouTube Data API v3. "
                f"Detalle: {message}"
            )
        if response.status_code == 404:
            raise YouTubeAPIError(f"Recurso no encontrado en YouTube: {message}")
        raise YouTubeAPIError(f"HTTP {response.status_code} de YouTube ({reason or 'sin motivo'}): {message}")

    def _paginate(
        self, endpoint: str, params: dict[str, Any], *, max_items: int
    ) -> tuple[list[dict[str, Any]], int, bool]:
        """Recorre páginas hasta ``max_items``. Devuelve (items, páginas, todo_cache)."""
        items: list[dict[str, Any]] = []
        pages = 0
        all_cached = True
        token: str | None = None
        max_items = max(1, int(max_items))

        while len(items) < max_items and pages < MAX_PAGES:
            page_params = dict(params)
            if token:
                page_params["pageToken"] = token
            payload, from_cache = self._request_page(endpoint, page_params)
            pages += 1
            all_cached = all_cached and from_cache
            items.extend(payload.get("items") or [])
            token = payload.get("nextPageToken")
            if not token:
                break

        return items[:max_items], pages, all_cached

    # ------------------------------------------------------------- público
    def search_videos(
        self,
        query: str,
        *,
        max_results: int = 10,
        order: str = "relevance",
        published_after_days: int | None = None,
        region_code: str | None = None,
    ) -> list[Video]:
        """Busca vídeos (100 unidades por página) y devuelve detalles completos."""
        query = (query or "").strip()
        if not query:
            raise YouTubeError("La búsqueda no puede estar vacía.")
        if order not in {"relevance", "date", "viewCount", "rating", "title"}:
            raise YouTubeError(f"Orden de búsqueda no soportado: {order}")
        max_results = max(1, min(int(max_results), 50))

        params: dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(MAX_PAGE_SIZE, max_results),
            "order": order,
            "regionCode": (region_code or self.region).upper(),
            "relevanceLanguage": self.language,
        }
        if published_after_days:
            cutoff = datetime.now(UTC) - timedelta(days=int(published_after_days))
            params["publishedAfter"] = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")

        items, _, _ = self._paginate("search", params, max_items=max_results)
        video_ids = [
            str((item.get("id") or {}).get("videoId"))
            for item in items
            if (item.get("id") or {}).get("videoId")
        ]
        if not video_ids:
            return []
        return self.get_videos(video_ids)

    def get_videos(self, video_ids: list[str]) -> list[Video]:
        """Detalles de vídeos por id (1 unidad por cada bloque de 50)."""
        ordered = [vid for vid in dict.fromkeys(video_ids) if vid]
        videos: list[Video] = []
        for start in range(0, len(ordered), MAX_PAGE_SIZE):
            chunk = ordered[start : start + MAX_PAGE_SIZE]
            payload, _ = self._request_page(
                "videos",
                {"part": "snippet,statistics,contentDetails", "id": ",".join(chunk), "maxResults": 50},
            )
            videos.extend(video_from_api(item) for item in payload.get("items") or [])
        return videos

    def get_channel(self, channel_id: str | None = None, *, handle: str | None = None) -> Channel:
        """Datos públicos de un canal por id o por handle (``@nombre``)."""
        if not channel_id and not handle:
            raise YouTubeError("Indica channel_id o handle.")
        params: dict[str, Any] = {"part": "snippet,statistics,contentDetails"}
        if channel_id:
            params["id"] = channel_id
        else:
            params["forHandle"] = (handle or "").lstrip("@")

        payload, _ = self._request_page("channels", params)
        items = payload.get("items") or []
        if not items:
            raise YouTubeError(
                f"No se encontró el canal '{channel_id or handle}'. Verifica el id (empieza por UC) o el handle."
            )
        return channel_from_api(items[0])

    def get_channel_videos(self, channel_id_or_handle: str, *, max_results: int = 20) -> list[Video]:
        """Últimos vídeos subidos por un canal (usa la playlist de subidas)."""
        reference = (channel_id_or_handle or "").strip()
        if not reference:
            raise YouTubeError("Indica el id o handle del canal.")
        channel = self.get_channel(handle=reference) if reference.startswith("@") else self.get_channel(reference)
        if not channel.uploads_playlist_id:
            raise YouTubeError(f"El canal '{channel.title}' no expone playlist de subidas.")
        max_results = max(1, min(int(max_results), 50))

        items, _, _ = self._paginate(
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": channel.uploads_playlist_id,
                "maxResults": min(MAX_PAGE_SIZE, max_results),
            },
            max_items=max_results,
        )
        ids = [str((item.get("contentDetails") or {}).get("videoId")) for item in items]
        return self.get_videos([vid for vid in ids if vid and vid != "None"])

    def quota_snapshot(self) -> dict[str, object]:
        return self.quota.snapshot()

    def close(self) -> None:
        self._session.close()
