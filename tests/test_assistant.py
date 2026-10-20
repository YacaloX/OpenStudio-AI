"""Pruebas del bucle del asistente con un proveedor falso."""

from __future__ import annotations

import pytest

from core.assistant import Assistant
from core.errors import ModelUnavailableError
from models import FakeProvider


def make_assistant(settings, store, registry, provider) -> Assistant:
    return Assistant(settings, provider, registry, store, session_id="default")


def test_plain_text_turn_is_persisted(settings, store, registry):
    provider = FakeProvider(["Hola, soy YacaloX."])
    assistant = make_assistant(settings, store, registry, provider)

    turn = assistant.chat("¿Quién eres?")

    assert turn.text == "Hola, soy YacaloX."
    assert turn.executions == []
    assert turn.stopped_by is None
    history = store.history()
    assert history == [
        {"role": "user", "content": "¿Quién eres?"},
        {"role": "assistant", "content": "Hola, soy YacaloX."},
    ]


def test_system_prompt_is_sent_and_not_persisted(settings, store, registry):
    assistant = make_assistant(settings, store, registry, FakeProvider(["ok"]))
    assistant.chat("hola")
    sent = provider_messages(assistant)
    assert sent[0]["role"] == "system"
    assert "YacaloX AI" in sent[0]["content"]
    assert all(m["role"] != "system" for m in store.history())


def test_tool_call_is_executed_and_result_fed_back(settings, store, registry):
    provider = FakeProvider(
        [
            FakeProvider.tool_call("memory_save", {"content": "Preferencia: formato corto", "kind": "preference"}),
            "He guardado tu preferencia.",
        ]
    )
    assistant = make_assistant(settings, store, registry, provider)

    turn = assistant.chat("Recuerda que prefiero formato corto")

    assert [e.name for e in turn.executions] == ["memory_save"]
    assert turn.executions[0].ok
    assert turn.iterations == 2
    assert store.count(kind="preference") == 1

    second_call = provider_messages(assistant, index=1)
    tool_messages = [m for m in second_call if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert '"saved": true' in tool_messages[0]["content"].replace("True", "true")


def test_unknown_tool_error_is_returned_to_model(settings, store, registry):
    provider = FakeProvider(
        [
            FakeProvider.tool_call("delete_everything", {}),
            "No puedo hacer eso, esa herramienta no existe.",
        ]
    )
    assistant = make_assistant(settings, store, registry, provider)

    turn = assistant.chat("borra todo")

    assert turn.executions[0].ok is False
    second_call = provider_messages(assistant, index=1)
    tool_message = next(m for m in second_call if m["role"] == "tool")
    assert "Herramienta desconocida" in tool_message["content"]


def test_sensitive_tool_denied_without_approver(settings, store, registry):
    store.add("note", "dato a borrar")
    provider = FakeProvider(
        [
            FakeProvider.tool_call("forget_memory", {"id": 1}),
            "Entendido, no borro nada.",
        ]
    )
    assistant = make_assistant(settings, store, registry, provider)

    turn = assistant.chat("borra la memoria 1")

    assert turn.executions[0].denied
    assert store.get(1) is not None


def test_approver_callback_is_used(settings, store, registry):
    store.add("note", "dato a borrar")
    approvals: list[str] = []

    def approve(call) -> bool:
        approvals.append(call.name)
        return True

    provider = FakeProvider(
        [
            FakeProvider.tool_call("forget_memory", {"id": 1}),
            "Borrado.",
        ]
    )
    assistant = make_assistant(settings, store, registry, provider)

    turn = assistant.chat("borra la memoria 1", approver=approve)

    assert approvals == ["forget_memory"]
    assert turn.executions[0].ok
    assert store.get(1) is None


def test_max_iterations_stops_the_loop(settings, store, registry):
    provider = FakeProvider(
        [FakeProvider.tool_call("project_context", {}, call_id=f"c{i}") for i in range(10)]
    )
    assistant = make_assistant(settings, store, registry, provider)

    turn = assistant.chat("¿cuál es el contexto?")

    assert turn.stopped_by == "max_tool_iterations"
    assert turn.iterations == settings.max_tool_iterations
    assert len(turn.executions) == settings.max_tool_iterations
    assert "límite de iteraciones" in turn.text


def test_empty_input_rejected(settings, store, registry):
    assistant = make_assistant(settings, store, registry, FakeProvider(["x"]))
    with pytest.raises(ValueError):
        assistant.chat("    ")


def test_too_long_input_rejected(settings, store, registry):
    assistant = make_assistant(settings, store, registry, FakeProvider(["x"]))
    with pytest.raises(ValueError):
        assistant.chat("a" * 8001)


def test_provider_error_propagates(settings, store, registry):
    provider = FakeProvider(healthy=False)
    assistant = make_assistant(settings, store, registry, provider)
    with pytest.raises(ModelUnavailableError):
        assistant.chat("hola")


def test_history_window_limits_context(settings, store, registry):
    for index in range(settings.history_window + 6):
        store.add_message("user", f"viejo {index}")
    assistant = make_assistant(settings, store, registry, FakeProvider(["ok"]))
    assistant.chat("nuevo")
    sent = provider_messages(assistant)
    contents = [m["content"] for m in sent if m["role"] == "user"]
    # El mensaje actual se persiste antes de construir la ventana: entra dentro del límite.
    assert len(contents) == settings.history_window
    assert contents[-1] == "nuevo"


def test_reset_session_clears_only_history(settings, store, registry):
    store.add("note", "memoria que debe quedar")
    assistant = make_assistant(settings, store, registry, FakeProvider(["ok"]))
    assistant.chat("hola")
    assert assistant.reset_session() == 2  # mensaje de usuario + respuesta
    assert store.history() == []
    assert store.count() == 1


def provider_messages(assistant: Assistant, index: int = -1) -> list[dict]:
    return assistant.provider.calls[index]["messages"]  # type: ignore[attr-defined]
