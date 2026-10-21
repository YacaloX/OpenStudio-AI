"""Interfaz de línea de comandos de YacaloX AI.

Uso::

    py main.py                     # chat interactivo
    py main.py --ask "texto"       # un solo turno y salida
    py main.py --demo              # sin Ollama: proveedor falso (pruebas)
    py main.py --memoria           # resumen de la memoria persistente
    py main.py --cuota             # consumo de cuota de YouTube de hoy
    py main.py --healthcheck       # comprueba Ollama, el modelo y YouTube
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from config import ConfigError, load_settings
from core.assistant import Assistant
from core.errors import YacaloXError, YouTubeError
from memory.db import Database
from memory.store import MemoryStore
from models import create_provider
from research.store import ResearchStore
from tools import build_default_registry
from youtube.client import YouTubeClient

BANNER = """\
============================================================
  YacaloX AI — asistente de estrategia de YouTube
  Memoria local (SQLite) + Ollama + YouTube Data API v3
  Comandos: /salir  /memoria  /buscar <texto>  /cuota  /borrar  /ayuda
============================================================"""


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build_assistant(settings) -> tuple[Assistant, YouTubeClient | None]:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    store = MemoryStore(db)
    research = ResearchStore(db)
    provider = create_provider(settings)

    youtube_client: YouTubeClient | None = None
    if settings.youtube_enabled:
        try:
            youtube_client = YouTubeClient(
                settings.youtube_api_key or "",
                db,
                daily_quota=settings.youtube_daily_quota,
                cache_ttl=settings.youtube_cache_ttl,
                timeout=settings.youtube_timeout,
                region=settings.youtube_region,
                language=settings.youtube_language,
            )
        except YouTubeError as exc:
            print(f"[youtube] deshabilitado: {exc}", file=sys.stderr)

    registry = build_default_registry(
        store, settings, youtube_client=youtube_client, research_store=research
    )
    return Assistant(settings, provider, registry, store), youtube_client


def _print_quota(client: YouTubeClient | None) -> None:
    if client is None:
        print("\nYouTube no está configurado (falta YOUTUBE_API_KEY en .env).")
        return
    snapshot = client.quota_snapshot()
    print(f"\nCuota de YouTube — día {snapshot['day_utc']} (UTC)")
    print(f"  Usadas: {snapshot['used']} / {snapshot['daily_limit']} unidades")
    print(f"  Restantes: {snapshot['remaining']}")
    by_endpoint = snapshot.get("by_endpoint")
    if isinstance(by_endpoint, dict):
        for endpoint, usage in by_endpoint.items():
            print(f"    {endpoint}: {usage['units']} unidades en {usage['calls']} llamadas")
    print(f"  {snapshot['note']}")


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


def _interactive(assistant: Assistant, youtube_client: YouTubeClient | None) -> int:
    print(BANNER)
    if youtube_client is None:
        print("  [aviso] YouTube no está configurado: falta YOUTUBE_API_KEY en .env.")
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
        if user == "/cuota":
            _print_quota(youtube_client)
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
  /cuota          Consumo de cuota de YouTube de hoy
  /borrar         Borra el historial de esta sesión (no la memoria)
  /ayuda          Esta ayuda
Cualquier otra cosa se envía al asistente.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YacaloX AI — asistente local de estrategia de YouTube")
    parser.add_argument("--ask", metavar="TEXTO", help="Un único turno sin modo interactivo")
    parser.add_argument("--demo", action="store_true", help="Usa el proveedor falso (sin Ollama)")
    parser.add_argument("--memoria", action="store_true", help="Muestra el resumen de memoria y sale")
    parser.add_argument("--cuota", action="store_true", help="Muestra el consumo de cuota de YouTube y sale")
    parser.add_argument("--healthcheck", action="store_true", help="Comprueba Ollama, el modelo y YouTube")
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
        assistant, youtube_client = build_assistant(settings)
    except YacaloXError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    try:
        if args.memoria:
            _print_memory(assistant)
            return 0

        if args.cuota:
            _print_quota(youtube_client)
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
                print(f"[healthcheck] Ollama OK. Modelos descargados: {', '.join(available) or 'ninguno'}")
                if settings.model not in available:
                    print(f"[healthcheck] AVISO: falta '{settings.model}'. Ejecuta: ollama pull {settings.model}")
                    return 1
            else:
                print("[healthcheck] Ollama OK (proveedor simulado).")

            if youtube_client is None:
                print("[healthcheck] YouTube: sin configurar (falta YOUTUBE_API_KEY).")
            else:
                try:
                    channel = youtube_client.get_channel("UC_x5XG1OV2P6uZZ5FSM9Ttw")
                except YacaloXError as exc:
                    print(f"[healthcheck] YouTube FALLO: {exc}", file=sys.stderr)
                    return 1
                print(f"[healthcheck] YouTube OK (prueba: {channel.title}, 1 unidad de cuota).")
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

        return _interactive(assistant, youtube_client)
    finally:
        assistant.close()
        if youtube_client is not None:
            youtube_client.close()


if __name__ == "__main__":
    sys.exit(main())
