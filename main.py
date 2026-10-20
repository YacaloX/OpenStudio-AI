"""Interfaz de línea de comandos de YacaloX AI (Fase 1).

Uso::

    py main.py                     # chat interactivo
    py main.py --ask "texto"       # un solo turno y salida
    py main.py --demo              # sin Ollama: proveedor falso (pruebas)
    py main.py --memoria           # resumen de la memoria persistente
    py main.py --healthcheck       # comprueba que Ollama y el modelo responden
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from config import ConfigError, load_settings
from core.assistant import Assistant
from core.errors import YacaloXError
from memory.store import MemoryStore
from models import create_provider
from tools import build_default_registry

BANNER = """\
============================================================
  YacaloX AI — asistente de estrategia de YouTube (Fase 1)
  Memoria local en SQLite + Ollama. Sin conexión a YouTube.
  Comandos: /salir  /memoria  /buscar <texto>  /borrar  /ayuda
============================================================"""


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build_assistant(settings) -> Assistant:
    settings.ensure_dirs()
    store = MemoryStore(settings.db_path)
    registry = build_default_registry(store, settings)
    provider = create_provider(settings)
    return Assistant(settings, provider, registry, store)


def _print_turn(turn) -> None:
    print(f"\nYacaloX [{turn.model or 'sin modelo'} | {turn.elapsed_ms} ms]\n")
    print(turn.text)
    if turn.executions:
        lines = []
        for exe in turn.executions:
            if exe.ok:
                lines.append(f"  + {exe.name}: ok")
            elif exe.denied:
                lines.append(f"  ! {exe.name}: denegada por el usuario")
            else:
                lines.append(f"  - {exe.name}: {exe.detail or 'error'}")
        print("\nHerramientas:\n" + "\n".join(lines))
    if turn.stopped_by == "max_tool_iterations":
        print("\n[aviso] Se alcanzó el límite de iteraciones de herramientas.")
    print()


def _interactive(assistant: Assistant) -> int:
    print(BANNER)
    while True:
        try:
            user = input("\nTú > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHasta pronto.")
            return 0
        if not user:
            continue
        if user in {"/salir", "/exit", "/quit"}:
            print("Hasta pronto.")
            return 0
        if user == "/ayuda":
            print(_HELP)
            continue
        if user == "/memoria":
            _print_memory(assistant)
            continue
        if user == "/borrar":
            assistant.reset_session()
            print("Historial de la sesión borrado. La memoria persistente se conserva.")
            continue
        if user.startswith("/buscar "):
            query = user[len("/buscar ") :].strip()
            results = assistant.store.search(query, limit=8)
            if not results:
                print("Sin resultados.")
            for record in results:
                print(f"  [{record.id}] {record.kind} (conf {record.confidence:.2f}): {record.content[:160]}")
            continue
        try:
            turn = assistant.chat(user, approver=_ask_approval)
        except YacaloXError as exc:
            print(f"\n[error] {exc}")
            continue
        except ValueError as exc:
            print(f"\n[entrada inválida] {exc}")
            continue
        _print_turn(turn)


def _ask_approval(call) -> bool:
    print(f"\n  ⚠ La herramienta '{call.name}' es sensible. Argumentos: "
          f"{json.dumps(call.arguments, ensure_ascii=False, default=str)}")
    answer = input("  ¿Autorizar? [s/N] > ").strip().lower()
    return answer in {"s", "si", "sí", "y", "yes"}


def _print_memory(assistant: Assistant) -> None:
    stats = assistant.store.stats()
    print("\nEstadísticas de memoria:")
    for key, value in sorted(stats.items()):
        print(f"  {key}: {value}")
    recent = assistant.store.recent(limit=10)
    if recent:
        print("\nÚltimas entradas:")
        for record in recent:
            print(f"  [{record.id}] {record.kind} (conf {record.confidence:.2f}) {record.content[:140]}")


_HELP = """
Comandos:
  /salir          Cierra la aplicación
  /memoria        Resumen de la memoria persistente
  /buscar <texto> Búsqueda léxica en la memoria
  /borrar         Borra el historial de esta sesión (no la memoria)
  /ayuda          Esta ayuda
Cualquier otra cosa se envía al asistente.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YacaloX AI — Fase 1 (asistente local)")
    parser.add_argument("--ask", metavar="TEXTO", help="Un único turno sin modo interactivo")
    parser.add_argument("--demo", action="store_true", help="Usa el proveedor falso (sin Ollama)")
    parser.add_argument("--memoria", action="store_true", help="Muestra el resumen de memoria y sale")
    parser.add_argument("--healthcheck", action="store_true", help="Comprueba la conexión con Ollama")
    parser.add_argument("--model", help="Sobrescribe YACALOX_MODEL para esta ejecución")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[configuración] {exc}", file=sys.stderr)
        return 2

    if args.demo:
        import os

        os.environ["YACALOX_PROVIDER"] = "fake"
        settings = load_settings(reload=True)

    if args.model:
        import os

        os.environ["YACALOX_MODEL"] = args.model
        settings = load_settings(reload=True)

    _setup_logging(settings.log_level)

    try:
        assistant = build_assistant(settings)
    except YacaloXError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    try:
        if args.memoria:
            _print_memory(assistant)
            return 0

        if args.healthcheck:
            try:
                assistant.healthcheck()
            except YacaloXError as exc:
                print(f"[healthcheck] FALLO: {exc}", file=sys.stderr)
                return 1
            from models.ollama import OllamaProvider

            if isinstance(assistant.provider, OllamaProvider):
                available = assistant.provider.available_models()
                print(f"[healthcheck] OK. Modelos descargados: {', '.join(available) or 'ninguno'}")
                if settings.model not in available:
                    print(f"[healthcheck] AVISO: falta '{settings.model}'. Ejecuta: ollama pull {settings.model}")
                    return 1
            else:
                print("[healthcheck] OK (proveedor simulado).")
            return 0

        if args.ask:
            try:
                turn = assistant.chat(args.ask, approver=lambda call: False)
            except YacaloXError as exc:
                print(f"[error] {exc}", file=sys.stderr)
                return 1
            print(turn.text)
            if turn.executions:
                summary = ", ".join(
                    f"{e.name}:{'ok' if e.ok else ('denegada' if e.denied else 'error')}"
                    for e in turn.executions
                )
                print(f"\n[herramientas] {summary}", file=sys.stderr)
            return 0

        return _interactive(assistant)
    finally:
        assistant.close()


if __name__ == "__main__":
    sys.exit(main())
