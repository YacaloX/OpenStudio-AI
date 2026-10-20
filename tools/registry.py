"""Registro y ejecución de herramientas del asistente.

El modelo sólo puede hacer lo que esté registrado aquí: no hay ejecución de
comandos del sistema, ni shell, ni escritura de archivos arbitrarios. Las
herramientas marcadas como ``sensitive`` exigen aprobación humana explícita y
quedan auditadas en SQLite.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.errors import ApprovalDenied
from memory.store import MemoryStore
from models.base import ToolCall

logger = logging.getLogger(__name__)

Approver = Callable[[ToolCall], bool]


@dataclass(frozen=True)
class ToolResult:
    """Resultado de una ejecución de herramienta, listo para devolver al modelo."""

    ok: bool
    data: Any = None
    error: str | None = None
    denied: bool = False

    @property
    def text(self) -> str:
        """Representación en texto que se envía como mensaje ``tool``."""
        if self.ok:
            try:
                return json.dumps(self.data, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                return str(self.data)
        prefix = "ACCESO DENEGADO POR EL USUARIO: " if self.denied else "ERROR: "
        return prefix + (self.error or "error desconocido")


@dataclass(frozen=True)
class ToolSpec:
    """Definición completa de una herramienta."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    sensitive: bool = False
    category: str = "general"

    def schema(self) -> dict[str, Any]:
        """Esquema en el formato que espera la API de Ollama/OpenAI."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Catálogo cerrado de herramientas expuestas al modelo."""

    def __init__(self, store: MemoryStore | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._store = store

    # ----------------------------------------------------------- registro
    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Herramienta duplicada: {spec.name}")
        if not spec.name or " " in spec.name:
            raise ValueError(f"Nombre de herramienta inválido: {spec.name!r}")
        self._tools[spec.name] = spec

    def tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        *,
        sensitive: bool = False,
        category: str = "general",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorador para registrar una función como herramienta."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register(
                ToolSpec(
                    name=name,
                    description=description,
                    parameters=parameters,
                    handler=lambda args: func(**args),
                    sensitive=sensitive,
                    category=category,
                )
            )
            return func

        return decorator

    # ------------------------------------------------------------- consultas
    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._tools[name].schema() for name in sorted(self._tools)]

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    # ------------------------------------------------------------ ejecución
    def execute(
        self,
        call: ToolCall,
        *,
        approver: Approver | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        """Ejecuta una llamada del modelo con todas las salvaguardas.

        - Herramienta desconocida → error (nunca se ejecuta nada improvisado).
        - Herramienta sensible sin aprobación → denegada y auditada.
        - Excepción de la herramienta → error capturado, el bucle continúa.
        """
        spec = self._tools.get(call.name)
        if spec is None:
            result = ToolResult(
                ok=False,
                error=f"Herramienta desconocida '{call.name}'. Disponibles: {', '.join(self.names())}",
            )
            self._audit(call, result)
            return result

        if not isinstance(call.arguments, dict):
            result = ToolResult(ok=False, error="Los argumentos deben ser un objeto JSON.")
            self._audit(call, result)
            return result

        if spec.sensitive:
            approved = False if approver is None else bool(approver(call))
            if not approved:
                result = ToolResult(
                    ok=False,
                    denied=True,
                    error=f"La herramienta sensible '{spec.name}' requiere aprobación explícita del usuario.",
                )
                self._audit(call, result)
                return result

        if dry_run:
            return ToolResult(ok=True, data={"dry_run": True, "tool": spec.name})

        try:
            data = spec.handler(call.arguments)
        except (ApprovalDenied, PermissionError) as exc:
            result = ToolResult(ok=False, denied=True, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - una herramienta nunca debe tumbar el bucle
            logger.exception("Fallo en la herramienta %s", spec.name)
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        else:
            result = ToolResult(ok=True, data=data)

        self._audit(call, result)
        return result

    def _audit(self, call: ToolCall, result: ToolResult) -> None:
        status = "ok" if result.ok else ("denied" if result.denied else "error")
        if self._store is not None:
            try:
                self._store.log_tool(call.name, call.arguments, status, result.error or result.text[:500])
            except Exception:  # noqa: BLE001 - la auditoría nunca debe bloquear la ejecución
                logger.exception("No se pudo auditar la llamada a %s", call.name)
        logger.info("tool=%s status=%s args=%s", call.name, status, _short(call.arguments))


def _short(args: dict[str, Any], limit: int = 200) -> str:
    try:
        text = json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(args)
    return text if len(text) <= limit else text[:limit] + "…"
