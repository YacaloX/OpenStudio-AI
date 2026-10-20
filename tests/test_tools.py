"""Pruebas del registro de herramientas y de las herramientas de la Fase 1."""

from __future__ import annotations

import pytest

from models.base import ToolCall
from tools.registry import ToolSpec


def _call(name: str, arguments: dict | None = None, call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments or {})


def test_default_registry_exposes_expected_tools(registry):
    names = registry.names()
    for expected in (
        "memory_search",
        "memory_save",
        "project_context",
        "generate_report",
        "clear_history",
        "forget_memory",
    ):
        assert expected in names
    assert len(registry) == 6


def test_schemas_are_ollama_shaped(registry):
    schemas = registry.schemas()
    assert all(s["type"] == "function" for s in schemas)
    save = next(s for s in schemas if s["function"]["name"] == "memory_save")
    assert "content" in save["function"]["parameters"]["properties"]
    assert "required" in save["function"]["parameters"]


def test_unknown_tool_is_rejected_and_audited(registry, store):
    result = registry.execute(_call("rm_rf", {"path": "/"}))
    assert not result.ok
    assert "desconocida" in result.error
    assert "memory_save" in result.error
    assert store.tool_calls()[0]["status"] == "error"


def test_non_dict_arguments_rejected(registry):
    result = registry.execute(ToolCall(id="c", name="memory_save", arguments=["not", "a", "dict"]))  # type: ignore[arg-type]
    assert not result.ok


def test_memory_save_and_search_roundtrip(registry, store):
    saved = registry.execute(_call("memory_save", {"content": "Los cortos rinden mejor", "kind": "analysis",
                                                   "confidence": 0.7, "tags": ["shorts"]}))
    assert saved.ok
    assert saved.data["saved"] is True

    found = registry.execute(_call("memory_search", {"query": "cortos"}))
    assert found.ok
    assert found.data["count"] == 1
    assert found.data["results"][0]["kind"] == "analysis"


def test_memory_save_invalid_kind_returns_error(registry):
    result = registry.execute(_call("memory_save", {"content": "x", "kind": "invalido"}))
    assert not result.ok
    assert "kind" in result.error or "ValueError" in result.error


def test_sensitive_tool_denied_without_approver(registry, store):
    store.add("note", "borrable")
    result = registry.execute(_call("forget_memory", {"id": 1}))
    assert not result.ok
    assert result.denied
    assert store.get(1) is not None  # no se borró
    assert store.tool_calls()[0]["status"] == "denied"


def test_sensitive_tool_denied_when_approver_says_no(registry, store):
    store.add("note", "borrable")
    result = registry.execute(_call("forget_memory", {"id": 1}), approver=lambda call: False)
    assert result.denied
    assert store.get(1) is not None


def test_sensitive_tool_executed_with_approval(registry, store):
    memory_id = store.add("note", "borrable")
    seen = []

    def approve(call: ToolCall) -> bool:
        seen.append(call.name)
        return True

    result = registry.execute(_call("forget_memory", {"id": memory_id}), approver=approve)
    assert result.ok
    assert result.data == {"deleted": True}
    assert seen == ["forget_memory"]
    assert store.get(memory_id) is None


def test_non_sensitive_tools_need_no_approval(registry):
    result = registry.execute(_call("project_context"))
    assert result.ok
    assert "now_utc" in result.data
    assert result.data["config"]["provider"] == "fake"


def test_handler_exception_is_captured(registry, store):
    registry.register(
        ToolSpec(
            name="siempre_falla",
            description="herramienta rota",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: 1 / 0,
        )
    )
    result = registry.execute(_call("siempre_falla"))
    assert not result.ok
    assert "ZeroDivisionError" in result.error
    assert store.tool_calls()[0]["status"] == "error"


def test_duplicate_registration_rejected(registry):
    with pytest.raises(ValueError):
        registry.register(
            ToolSpec(
                name="memory_save",
                description="duplicada",
                parameters={"type": "object", "properties": {}},
                handler=lambda args: None,
            )
        )


def test_generate_report_content(store, registry):
    store.add("research", "Hallazgo con fuente", source="YouTube Data API", confidence=0.9)
    store.add("idea", "Idea de vídeo", confidence=0.4)
    result = registry.execute(_call("generate_report", {"title": "Informe semanal"}))
    assert result.ok
    markdown = result.data["markdown"]
    assert markdown.startswith("# Informe semanal")
    assert "## research" in markdown
    assert "Hallazgo con fuente" in markdown
    assert "verifica la fuente" in markdown


def test_report_empty_memory(store, registry):
    result = registry.execute(_call("generate_report", {"title": "Vacío"}))
    assert result.ok
    assert "No hay memoria" in result.data["markdown"]
