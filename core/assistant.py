"""Bucle del asistente: modelo + herramientas + memoria.

Flujo de cada turno:

1. Cargar historial persistido de la sesión.
2. Enviar mensajes al proveedor con los esquemas de las herramientas.
3. Si el modelo pide herramientas: aprobación (si son sensibles), ejecución y
   devolución del resultado como mensaje ``tool``.
4. Repetir hasta que responda con texto o se alcance el límite de iteraciones.
5. Persistir la respuesta final.

No hay ejecución arbitraria: sólo se admite lo que esté en el ``ToolRegistry``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from config import Settings
from core.errors import ModelError, ToolNotFoundError
from core.prompts import build_system_prompt
from memory.store import MemoryStore
from models.base import LLMProvider, ToolCall
from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ToolExecution:
    """Resultado de una herramienta ejecutada en este turno."""

    name: str
    ok: bool
    denied: bool = False
    detail: str = ""


@dataclass
class Turn:
    """Respuesta completa de un turno del asistente."""

    text: str
    executions: list[ToolExecution] = field(default_factory=list)
    stopped_by: str | None = None
    model: str = ""
    elapsed_ms: int = 0
    iterations: int = 0


class Assistant:
    """Coordina proveedor de lenguaje, herramientas y memoria."""

    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider,
        registry: ToolRegistry,
        store: MemoryStore,
        *,
        session_id: str = "default",
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.registry = registry
        self.store = store
        self.session_id = session_id
        self._system_prompt = build_system_prompt(settings)

    # ------------------------------------------------------------- utilidades
    def healthcheck(self) -> None:
        self.provider.healthcheck()

    def reset_session(self) -> int:
        """Borra el historial de la sesión (acción del usuario, no del modelo)."""
        return self.store.clear_history(session_id=self.session_id)

    # ------------------------------------------------------------------ chat
    def chat(self, user_input: str, *, approver=None) -> Turn:
        text = (user_input or "").strip()
        if not text:
            raise ValueError("El mensaje del usuario está vacío.")
        if len(text) > 8000:
            raise ValueError("El mensaje supera los 8000 caracteres; acórtalo.")

        started = time.perf_counter()
        self.store.add_message("user", text, session_id=self.session_id)

        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system_prompt}]
        messages.extend(self.store.history(session_id=self.session_id, limit=self.settings.history_window))
        tools = self.registry.schemas() if self.provider.supports_tools() else None

        executions: list[ToolExecution] = []
        response_text = ""
        model_name = ""
        stopped_by: str | None = None
        iterations = 0

        for iteration in range(1, self.settings.max_tool_iterations + 1):
            iterations = iteration
            response = self.provider.complete(messages, tools=tools)
            model_name = response.model or model_name

            if not response.tool_calls:
                response_text = response.text.strip()
                break

            messages.append(_assistant_tool_message(response.text, response.tool_calls))
            for call in response.tool_calls:
                result = self.registry.execute(call, approver=approver)
                executions.append(
                    ToolExecution(
                        name=call.name,
                        ok=result.ok,
                        denied=result.denied,
                        detail=(result.error or "")[:300],
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "content": result.text,
                        "tool_call_id": call.id,
                        "name": call.name,
                    }
                )
        else:
            stopped_by = "max_tool_iterations"
            response_text = (
                (response_text or "").strip()
                or "(Sin respuesta final: se alcanzó el límite de iteraciones de herramientas.)"
            )

        if not response_text:
            response_text = "(El modelo no devolvió texto tras usar las herramientas.)"
            stopped_by = stopped_by or "empty_response"

        self.store.add_message(
            "assistant", response_text, session_id=self.session_id, model=model_name or None
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info(
            "turno ok iterations=%d tools=%d %dms", iterations, len(executions), elapsed
        )
        return Turn(
            text=response_text,
            executions=executions,
            stopped_by=stopped_by,
            model=model_name,
            elapsed_ms=elapsed,
            iterations=iterations,
        )

    def run_once(self, user_input: str, *, approver=None) -> Turn:
        """Alias explícito para uso no interactivo (pruebas, CLI --ask)."""
        return self.chat(user_input, approver=approver)

    def close(self) -> None:
        self.provider.close()
        self.store.close()


def _assistant_tool_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    """Mensaje de asistente en el formato que espera la API de Ollama."""
    return {
        "role": "assistant",
        "content": text or "",
        "tool_calls": [
            {"function": {"name": call.name, "arguments": call.arguments}} for call in calls
        ],
    }


__all__ = ["Assistant", "ToolExecution", "Turn", "ModelError", "ToolNotFoundError"]
