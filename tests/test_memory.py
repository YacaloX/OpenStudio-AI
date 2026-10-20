"""Pruebas de la memoria persistente en SQLite."""

from __future__ import annotations

import pytest

from memory import Database, MemoryStore


def test_add_and_get_memory(store):
    memory_id = store.add("research", "Los shorts de tutoriales crecen en mi canal", source="analítica propia",
                          confidence=0.8, tags=["shorts", "tutoriales"])
    assert memory_id > 0
    record = store.get(memory_id)
    assert record is not None
    assert record.kind == "research"
    assert record.confidence == pytest.approx(0.8)
    assert record.tags == ["shorts", "tutoriales"]
    assert record.source == "analítica propia"


def test_invalid_kind_rejected(store):
    with pytest.raises(ValueError):
        store.add("cualquier_cosa", "contenido")


def test_empty_content_rejected(store):
    with pytest.raises(ValueError):
        store.add("note", "   ")


def test_confidence_is_clamped(store):
    low = store.add("conclusion", "confianza muy baja", confidence=-3)
    high = store.add("conclusion", "confianza muy alta", confidence=9)
    assert store.get(low).confidence == 0.0
    assert store.get(high).confidence == 1.0


def test_search_requires_matching_tokens(store):
    store.add("research", "La audiencia de Minecraft en español creció en 2026", tags=["minecraft"])
    store.add("idea", "Vídeo de recetas fáciles para principiantes", tags=["cocina"])
    hits = store.search("minecraft español")
    assert len(hits) == 1
    assert "Minecraft" in hits[0].content
    assert store.search("tema inexistente") == []


def test_search_filters_by_kind(store):
    store.add("idea", "Idea sobre resumen de noticias")
    store.add("research", "Investigación sobre noticias semanales")
    hits = store.search("noticias", kind="idea")
    assert len(hits) == 1
    assert hits[0].kind == "idea"


def test_search_without_tokens_returns_recent(store):
    store.add("note", "primera nota")
    store.add("note", "segunda nota")
    recent = store.search("  ", limit=5)
    assert len(recent) == 2


def test_search_ignores_short_tokens_and_falls_back_to_recent(store):
    store.add("note", "un video de mi canal")
    # Sin tokens de 3+ caracteres la búsqueda degrada a "recientes".
    assert len(store.search("de un")) == 1


def test_update_and_delete(store):
    memory_id = store.add("idea", "idea original", confidence=0.2)
    assert store.update(memory_id, content="idea mejorada", confidence=0.9)
    record = store.get(memory_id)
    assert record.content == "idea mejorada"
    assert record.confidence == pytest.approx(0.9)
    assert store.delete(memory_id)
    assert store.get(memory_id) is None
    assert not store.delete(memory_id)


def test_conversation_history_order_and_limit(store):
    for index in range(5):
        store.add_message("user", f"mensaje {index}")
        store.add_message("assistant", f"respuesta {index}")
    history = store.history(limit=4)
    assert len(history) == 4
    # Sólo caben las 4 últimas filas: m3, a3, m4, a4.
    assert history[0]["content"] == "mensaje 3"
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_history_excludes_system_and_clears(store):
    store.add_message("system", "prompt")
    store.add_message("user", "hola")
    assert len(store.history()) == 1
    assert store.clear_history() == 2  # limpia toda la sesión, incluida la fila system
    assert store.history() == []


def test_invalid_role_rejected(store):
    with pytest.raises(ValueError):
        store.add_message("hacker", "x")


def test_tool_log_and_stats(store):
    store.add("idea", "idea X")
    store.log_tool("memory_search", {"query": "idea"}, "ok")
    store.log_tool("clear_history", {}, "denied", "usuario dijo no")
    tools = store.tool_calls()
    assert tools[0]["status"] == "denied"
    stats = store.stats()
    assert stats["memories_total"] == 1
    assert stats["memories_idea"] == 1
    assert stats["tool_calls"] == 2
    assert stats["messages"] == 0


def test_database_schema_version_and_persistence(tmp_path):
    path = tmp_path / "persist.db"
    store = MemoryStore(path)
    store.add("preference", "idioma español")
    store.close()

    reopened = MemoryStore(path)
    assert reopened.db.schema_version() == 1
    assert reopened.count() == 1
    reopened.close()


def test_in_memory_database_works():
    store = MemoryStore(Database(":memory:"))
    store.add("note", "nota en memoria")
    assert store.count() == 1
    store.close()
