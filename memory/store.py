"""Memoria persistente: investigaciones, ideas, preferencias y conclusiones.

Todo lo que el asistente «recuerda» entre sesiones pasa por aquí. La
recuperación de la Fase 1 es léxica (tokenización simple + LIKE): sencilla,
rápida y sin descargar modelos de embeddings. Los embeddings se podrán añadir
más adelante sin cambiar esta interfaz.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from memory.db import Database, utc_now

VALID_KINDS = (
    "research",      # hallazgos de investigación con fuente
    "idea",          # ideas de vídeo y su estado
    "preference",    # configuración y gustos del usuario
    "analysis",      # análisis de datos ya realizados
    "result",        # resultados reales de vídeos publicados
    "conclusion",    # conclusiones con su nivel de confianza
    "note",          # notas libres
)

_TOKEN_RE = re.compile(r"[a-z0-9áéíóúñü]{3,}")


@dataclass(frozen=True)
class MemoryRecord:
    """Una entrada de memoria recuperada."""

    id: int
    kind: str
    content: str
    source: str | None
    confidence: float
    tags: list[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> MemoryRecord:
        try:
            tags = json.loads(row["tags"])
        except (TypeError, json.JSONDecodeError):
            tags = []
        return cls(
            id=int(row["id"]),
            kind=str(row["kind"]),
            content=str(row["content"]),
            source=row["source"],
            confidence=float(row["confidence"]),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


class MemoryStore:
    """API de alto nivel sobre las tablas de memoria, conversaciones y auditoría."""

    def __init__(self, db: Database | Path | str) -> None:
        self.db = db if isinstance(db, Database) else Database(db)

    # ------------------------------------------------------------- memorias
    def add(
        self,
        kind: str,
        content: str,
        *,
        source: str | None = None,
        confidence: float = 0.5,
        tags: list[str] | None = None,
    ) -> int:
        if kind not in VALID_KINDS:
            raise ValueError(f"kind='{kind}' inválido. Usa uno de: {', '.join(VALID_KINDS)}.")
        text = (content or "").strip()
        if not text:
            raise ValueError("El contenido de la memoria no puede estar vacío.")
        confidence = max(0.0, min(1.0, float(confidence)))
        now = utc_now()
        payload = json.dumps(tags or [], ensure_ascii=False)
        cursor = self.db.execute(
            "INSERT INTO memories(kind, content, source, confidence, tags, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (kind, text, source, confidence, payload, now, now),
        )
        return int(cursor.lastrowid or 0)

    def get(self, memory_id: int) -> MemoryRecord | None:
        rows = self.db.query("SELECT * FROM memories WHERE id=?", (memory_id,))
        return MemoryRecord.from_row(rows[0]) if rows else None

    def update(self, memory_id: int, *, content: str | None = None, confidence: float | None = None) -> bool:
        record = self.get(memory_id)
        if record is None:
            return False
        new_content = record.content if content is None else content.strip()
        new_conf = record.confidence if confidence is None else max(0.0, min(1.0, confidence))
        self.db.execute(
            "UPDATE memories SET content=?, confidence=?, updated_at=? WHERE id=?",
            (new_content, new_conf, utc_now(), memory_id),
        )
        return True

    def delete(self, memory_id: int) -> bool:
        cursor = self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return cursor.rowcount > 0

    def search(self, query: str, *, kind: str | None = None, limit: int = 8) -> list[MemoryRecord]:
        """Búsqueda léxica: intersecta los términos de la consulta con el contenido."""
        tokens = _TOKEN_RE.findall((query or "").lower())
        limit = max(1, min(int(limit), 50))
        if not tokens:
            return self.recent(kind=kind, limit=limit)

        conditions = []
        params: list[object] = []
        for token in tokens:
            conditions.append("(lower(content) LIKE ? OR lower(COALESCE(source,'')) LIKE ? OR lower(tags) LIKE ?)")
            like = f"%{token}%"
            params.extend([like, like, like])
        where = " AND ".join(conditions)
        if kind:
            where += " AND kind=?"
            params.append(kind)
        params.append(limit)
        rows = self.db.query(
            f"SELECT * FROM memories WHERE {where} ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        )
        return [MemoryRecord.from_row(r) for r in rows]

    def recent(self, *, kind: str | None = None, limit: int = 10) -> list[MemoryRecord]:
        limit = max(1, min(int(limit), 100))
        if kind:
            rows = self.db.query(
                "SELECT * FROM memories WHERE kind=? ORDER BY updated_at DESC LIMIT ?", (kind, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [MemoryRecord.from_row(r) for r in rows]

    def count(self, *, kind: str | None = None) -> int:
        if kind:
            rows = self.db.query("SELECT COUNT(*) AS n FROM memories WHERE kind=?", (kind,))
        else:
            rows = self.db.query("SELECT COUNT(*) AS n FROM memories")
        return int(rows[0]["n"])

    # --------------------------------------------------------- conversación
    def add_message(self, role: str, content: str, *, session_id: str = "default", model: str | None = None) -> int:
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"Rol de conversación inválido: {role}")
        cursor = self.db.execute(
            "INSERT INTO conversations(session_id, role, content, model, created_at) VALUES(?,?,?,?,?)",
            (session_id, role, content[:20000], model, utc_now()),
        )
        return int(cursor.lastrowid or 0)

    def history(self, *, session_id: str = "default", limit: int = 12) -> list[dict[str, str]]:
        """Últimos mensajes (sólo user/assistant) listos para enviar al modelo."""
        limit = max(2, min(int(limit), 200))
        rows = self.db.query(
            "SELECT role, content FROM conversations WHERE session_id=? AND role IN ('user','assistant')"
            " ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        return [{"role": str(r["role"]), "content": str(r["content"])} for r in reversed(rows)]

    def clear_history(self, *, session_id: str = "default") -> int:
        cursor = self.db.execute("DELETE FROM conversations WHERE session_id=?", (session_id,))
        return cursor.rowcount

    # ------------------------------------------------------------ auditoría
    def log_tool(self, name: str, arguments: dict, status: str, detail: str | None = None) -> int:
        cursor = self.db.execute(
            "INSERT INTO tool_log(tool_name, arguments, status, detail, created_at) VALUES(?,?,?,?,?)",
            (name, json.dumps(arguments, ensure_ascii=False, default=str), status, detail, utc_now()),
        )
        return int(cursor.lastrowid or 0)

    def tool_calls(self, *, limit: int = 20) -> list[dict[str, str]]:
        rows = self.db.query("SELECT * FROM tool_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),))
        return [
            {
                "id": str(r["id"]),
                "tool": str(r["tool_name"]),
                "arguments": str(r["arguments"]),
                "status": str(r["status"]),
                "detail": str(r["detail"] or ""),
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ]

    def stats(self) -> dict[str, int]:
        rows = self.db.query("SELECT kind, COUNT(*) AS n FROM memories GROUP BY kind")
        counts = {str(r["kind"]): int(r["n"]) for r in rows}
        total_messages = self.db.query("SELECT COUNT(*) AS n FROM conversations")[0]["n"]
        total_tools = self.db.query("SELECT COUNT(*) AS n FROM tool_log")[0]["n"]
        return {
            "memories_total": sum(counts.values()),
            "messages": int(total_messages),
            "tool_calls": int(total_tools),
            **{f"memories_{k}": v for k, v in counts.items()},
        }

    def close(self) -> None:
        self.db.close()
