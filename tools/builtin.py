"""Herramientas de la Fase 1: memoria, contexto del proyecto e informes.

Ninguna de estas herramientas toca la red ni ejecuta procesos: sólo leen y
escriben la base local y devuelven información de contexto.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from config import Settings
from memory.store import VALID_KINDS, MemoryStore
from tools.registry import ToolRegistry

_KINDS_HELP = ", ".join(VALID_KINDS)

_PARAM_KIND = {
    "type": "string",
    "description": f"Categoría. Uno de: {_KINDS_HELP}.",
    "enum": list(VALID_KINDS),
}


def build_default_registry(store: MemoryStore, settings: Settings) -> ToolRegistry:
    """Crea el registro de herramientas disponible en la Fase 1."""
    registry = ToolRegistry(store=store)

    registry.register(
        _spec(
            name="memory_search",
            description=(
                "Busca en la memoria persistente (investigaciones, ideas, preferencias, análisis, "
                "resultados, conclusiones) usando palabras clave. Úsala antes de responder preguntas "
                "sobre lo que ya se ha investigado o decidido previamente."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Texto de búsqueda."},
                    "kind": {**_PARAM_KIND, "description": f"Filtro opcional. {_PARAM_KIND['description']}"},
                    "limit": {"type": "integer", "description": "Máximo de resultados (1-20).", "default": 8},
                },
                "required": ["query"],
            },
            category="memory",
            handler=lambda args: _memory_search(store, args),
        )
    )

    registry.register(
        _spec(
            name="memory_save",
            description=(
                "Guarda un hallazgo, idea, preferencia o conclusión en la memoria persistente para "
                "usarlo en futuras sesiones. Incluye la fuente cuando exista y asigna un nivel de "
                "confianza realista (0=especulación, 1=dato verificado)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Contenido a recordar."},
                    "kind": _PARAM_KIND,
                    "source": {"type": "string", "description": "Fuente del dato (URL, 'analítica propia', ...)."},
                    "confidence": {
                        "type": "number",
                        "description": "Nivel de confianza de 0 a 1.",
                        "minimum": 0,
                        "maximum": 1,
                        "default": 0.5,
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Etiquetas cortas para agrupar.",
                    },
                },
                "required": ["content"],
            },
            category="memory",
            handler=lambda args: _memory_save(store, args),
        )
    )

    registry.register(
        _spec(
            name="project_context",
            description=(
                "Devuelve el contexto del proyecto: configuración activa, fecha y hora actuales y "
                "conteo de la memoria. Úsala para responder sobre el estado del sistema o la fecha."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            category="context",
            handler=lambda args: _project_context(settings, store),
        )
    )

    registry.register(
        _spec(
            name="generate_report",
            description=(
                "Compila un informe en Markdown a partir de la memoria guardada: resume las entradas "
                "por categoría con sus fuentes y niveles de confianza. Devuelve el texto del informe."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Título del informe."},
                    "kind": {**_PARAM_KIND, "description": f"Categoría a incluir. {_PARAM_KIND['description']}"},
                    "limit": {"type": "integer", "description": "Entradas a incluir (1-50).", "default": 20},
                },
                "required": ["title"],
            },
            category="report",
            handler=lambda args: _generate_report(store, args),
        )
    )

    registry.register(
        _spec(
            name="clear_history",
            description=(
                "Borra el historial de conversación de la sesión actual. Acción destructiva: "
                "siempre pide confirmación al usuario antes de ejecutarla."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            category="session",
            sensitive=True,
            handler=lambda args: {"deleted_messages": store.clear_history()},
        )
    )

    registry.register(
        _spec(
            name="forget_memory",
            description=(
                "Elimina de forma permanente una entrada de memoria por su id. Acción destructiva: "
                "siempre pide confirmación al usuario. Úsala sólo si el usuario lo pide expresamente."
            ),
            parameters={
                "type": "object",
                "properties": {"id": {"type": "integer", "description": "Id de la memoria a borrar."}},
                "required": ["id"],
            },
            category="memory",
            sensitive=True,
            handler=lambda args: {"deleted": store.delete(int(args["id"]))},
        )
    )

    return registry


def _spec(name: str, description: str, parameters: dict[str, Any], *, category: str,
          handler, sensitive: bool = False):
    from tools.registry import ToolSpec

    return ToolSpec(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
        sensitive=sensitive,
        category=category,
    )


# ------------------------------------------------------------------ manejadores
def _memory_search(store: MemoryStore, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", ""))
    kind = args.get("kind")
    limit = int(args.get("limit", 8) or 8)
    results = store.search(query, kind=str(kind) if kind else None, limit=limit)
    if not results:
        return {"results": [], "note": "Sin coincidencias en la memoria local."}
    return {
        "results": [
            {
                "id": r.id,
                "kind": r.kind,
                "content": r.content,
                "source": r.source,
                "confidence": r.confidence,
                "tags": r.tags,
                "updated_at": r.updated_at,
            }
            for r in results
        ],
        "count": len(results),
    }


def _memory_save(store: MemoryStore, args: dict[str, Any]) -> dict[str, Any]:
    memory_id = store.add(
        str(args.get("kind", "note")),
        str(args.get("content", "")),
        source=args.get("source"),
        confidence=float(args.get("confidence", 0.5) or 0.5),
        tags=[str(t) for t in args.get("tags") or []],
    )
    return {"saved": True, "id": memory_id}


def _project_context(settings: Settings, store: MemoryStore) -> dict[str, Any]:
    return {
        "now_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "now_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": settings.summary(),
        "memory_stats": store.stats(),
        "available_tools_note": "YouTube: fase 2 (API key pendiente). Analytics: fase 3 (OAuth pendiente).",
    }


def _generate_report(store: MemoryStore, args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title", "Informe")).strip() or "Informe"
    kind = args.get("kind")
    limit = int(args.get("limit", 20) or 20)
    records = store.recent(kind=str(kind) if kind else None, limit=limit)

    lines = [f"# {title}", "", f"_Generado: {datetime.now(UTC).isoformat(timespec='seconds')}_", ""]
    if not records:
        lines.append("_No hay memoria que incluir todavía._")
    else:
        by_kind: dict[str, list] = {}
        for record in records:
            by_kind.setdefault(record.kind, []).append(record)
        for current_kind in sorted(by_kind):
            lines.append(f"## {current_kind}")
            lines.append("")
            for record in by_kind[current_kind]:
                source = f" · fuente: {record.source}" if record.source else ""
                lines.append(f"- (confianza {record.confidence:.2f}{source}) {record.content}")
            lines.append("")
    lines.append(f"_Total de entradas: {len(records)}. Las conclusiones no son datos observados; "
                 "verifica la fuente antes de actuar._")
    return {"title": title, "markdown": "\n".join(lines), "entries": len(records)}
