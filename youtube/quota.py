"""Control local de cuota de la YouTube Data API v3.

Google cobra unidades por llamada (``search.list`` cuesta 100, el resto de
endpoints usados aquí cuesta 1). Este módulo lleva la cuenta por día UTC en
SQLite para no quedarse sin cuota a mitad de una investigación y para poder
mostrar el consumo al usuario.

La cuota real vive en Google Cloud; aquí sólo se evita pasarse del límite
configurado (``YACALOX_YOUTUBE_DAILY_QUOTA``, 10 000 por defecto).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.errors import YouTubeQuotaError
from memory.db import Database

logger = logging.getLogger(__name__)

UNIT_COSTS: dict[str, int] = {
    "search": 100,
    "videos": 1,
    "channels": 1,
    "playlistItems": 1,
}


def utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class QuotaManager:
    """Contabilidad de unidades consumidas por endpoint y día."""

    def __init__(self, db: Database, daily_limit: int = 10000) -> None:
        self.db = db
        self.daily_limit = int(daily_limit)

    def usage(self, day: str | None = None) -> dict[str, dict[str, int]]:
        target = day or utc_day()
        rows = self.db.query(
            "SELECT endpoint, units, calls FROM api_quota WHERE day=? ORDER BY endpoint", (target,)
        )
        return {str(r["endpoint"]): {"units": int(r["units"]), "calls": int(r["calls"])} for r in rows}

    def used(self, day: str | None = None) -> int:
        return sum(entry["units"] for entry in self.usage(day).values())

    def remaining(self, day: str | None = None) -> int:
        return max(0, self.daily_limit - self.used(day))

    def cost_of(self, endpoint: str) -> int:
        return UNIT_COSTS.get(endpoint, 1)

    def charge(self, endpoint: str, *, cost: int | None = None) -> int:
        """Reserva unidades antes de llamar a la API.

        Lanza :class:`YouTubeQuotaError` si la llamada superaría el límite local.
        """
        price = self.cost_of(endpoint) if cost is None else int(cost)
        if price > self.remaining():
            raise YouTubeQuotaError(
                f"Cuota local insuficiente: quedan {self.remaining()} unidades de {self.daily_limit} "
                f"y esta llamada a '{endpoint}' cuesta {price}. Espera al reinicio diario (UTC) o "
                "aumenta YACALOX_YOUTUBE_DAILY_QUOTA si tu proyecto tiene más cuota."
            )
        day = utc_day()
        self.db.execute(
            "INSERT INTO api_quota(day, endpoint, units, calls) VALUES(?,?,?,1)"
            " ON CONFLICT(day, endpoint) DO UPDATE SET units=units+?, calls=calls+1",
            (day, endpoint, price, price),
        )
        logger.debug("cuota: %s -%d unidades (quedan %d)", endpoint, price, self.remaining())
        return price

    def snapshot(self) -> dict[str, object]:
        usage = self.usage()
        used = sum(entry["units"] for entry in usage.values())
        return {
            "day_utc": utc_day(),
            "daily_limit": self.daily_limit,
            "used": used,
            "remaining": max(0, self.daily_limit - used),
            "by_endpoint": usage,
            "note": "Cuota estimada localmente; el límite real lo impone Google Cloud.",
        }
