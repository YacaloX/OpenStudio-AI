# OpenStudio-AI

Asistente personal de investigación y estrategia de contenido de YouTube.
Sistema modular: modelo de lenguaje local (Ollama), memoria persistente en SQLite,
herramientas con aprobación humana y, en fases posteriores, integración con las
APIs de YouTube.

**Estado: Fase 1 (MVP local) implementada y verificada.**

---

## 1. Requisitos

| Requisito | Detalle |
|---|---|
| Python | 3.11 o superior (probado con 3.14.6) |
| Ollama | Instalado y en ejecución (`ollama serve` o la app de bandeja) |
| Modelo | `qwen3:1.7b` (~1,4 GB, Q4_K_M) |
| Disco | ~3 GB libres (instalador de Ollama + modelo) |
| RAM | 8 GB suficientes; el modelo ocupa ~1,5 GB en memoria |
| GPU | **No necesaria.** La inferencia es por CPU |

Dependencias Python de la Fase 1: **sólo `requests`** (más `pytest` y `ruff` para
desarrollo). Si ya están instaladas, no hay nada que instalar:

```powershell
pip install -r requirements.txt
```

### Instalar Ollama y el modelo

```powershell
winget install --id Ollama.Ollama -e
ollama pull qwen3:1.7b
```

---

## 2. Configuración

```powershell
Copy-Item .env.example .env
```

Edita `.env` (nunca se sube a git; `.gitignore` lo excluye):

| Variable | Por defecto | Descripción |
|---|---|---|
| `YACALOX_PROVIDER` | `ollama` | `ollama` o `fake` (proveedor simulado para pruebas) |
| `YACALOX_MODEL` | `qwen3:1.7b` | Modelo de Ollama a utilizar |
| `YACALOX_OLLAMA_URL` | `http://127.0.0.1:11434` | API local de Ollama |
| `YACALOX_TIMEOUT` | `120` | Segundos máximos de espera por respuesta |
| `YACALOX_THINK` | `false` | Desactiva el «pensamiento» interno (más rápido) |
| `YACALOX_MAX_TOOL_ITERATIONS` | `6` | Iteraciones de herramientas por turno |
| `YACALOX_HISTORY_WINDOW` | `12` | Mensajes enviados como contexto |
| `YACALOX_TEMPERATURE` | `0.4` | Temperatura de muestreo (0–2) |
| `YACALOX_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `YACALOX_DATA_DIR` | `data` | Carpeta local de datos (SQLite) |
| `YOUTUBE_API_KEY` | vacío | **Fase 2**: YouTube Data API v3 |
| `YOUTUBE_CLIENT_SECRETS` | `data/client_secret.json` | **Fase 3**: OAuth 2.0 Analytics |

La configuración se valida al arrancar: valores fuera de rango o mal formados
detienen el programa con un mensaje que dice exactamente qué corregir.

### Claves de API (fases futuras)

- **YouTube Data API v3**: en Google Cloud Console → crear proyecto → habilitar
  *YouTube Data API v3* → *Credenciales* → *Clave de API*. Pegar el valor en
  `YOUTUBE_API_KEY` dentro de `.env`. Cuota diaria estándar: 10 000 unidades.
- **YouTube Analytics API**: requiere *OAuth 2.0*. Descargar el archivo de
  cliente (`client_secret*.json`), guardarlo como `data/client_secret.json`
  (excluido de git) y autorizar el acceso en la primera ejecución de la Fase 3.
  El acceso se puede revocar en https://myaccount.google.com/permissions.

Ninguna clave se escribe en el código ni se commitea.

---

## 3. Uso

```powershell
py main.py                     # chat interactivo
py main.py --ask "texto"       # un solo turno y salida
py main.py --healthcheck       # comprueba Ollama y el modelo
py main.py --memoria           # resumen de la memoria persistente
py main.py --demo              # sin Ollama: proveedor simulado
py main.py --model qwen3:0.6b  # sobrescribe el modelo para esta ejecución
```

Comandos del chat: `/memoria`, `/buscar <texto>`, `/borrar` (historial de la
sesión), `/ayuda`, `/salir`.

Las herramientas marcadas como sensibles (borrar memorias, borrar historial)
preguntan antes de ejecutarse. En modo `--ask` siempre se deniegan.

---

## 4. Pruebas

```powershell
py -m pytest        # 68 pruebas
py -m ruff check .  # lint
```

Las pruebas no usan red ni Ollama: el proveedor estándar de los tests es
`FakeProvider`, y el cliente Ollama se prueba con una sesión HTTP simulada.

---

## 5. Rendimiento medido (no estimado)

Medido en el equipo de destino: Xeon X3470 (4C/8T, sin AVX2), 8 GB DDR3,
Windows 11, sin uso de GPU.

| Métrica | Valor |
|---|---|
| Descarga de `qwen3:1.7b` | 1,4 GB (~1,3 MB/s en esa sesión) |
| RAM ocupada por el modelo | ~1,5 GB |
| Primera generación (carga del modelo) | 30–45 s |
| Velocidad sostenida | **~4 tokens/s** |
| Respuesta corta de 40–60 tokens | 10–30 s (según contexto) |
| CPU/GPU usados | CPU al 100 % en un subconjunto de hilos; GPU no participa |

Implicaciones prácticas: pide respuestas cortas (el prompt de sistema ya lo
hace), evita generar guiones largos de una sola vez y prefiere respuestas por
partes. Si necesitas más velocidad, `qwen3:0.6b` es ~3× más rápido pero peor
razonando; puedes probarlo con `py main.py --model qwen3:0.6b` sin modificar
nada más.

---

## 6. Estructura

```
YacaloX-AI/
├── config.py           # configuración validada desde entorno/.env
├── main.py             # CLI (interactivo, --ask, --healthcheck, --memoria)
├── core/
│   ├── errors.py       # errores de dominio
│   ├── prompts.py      # prompt de sistema
│   └── assistant.py    # bucle: modelo → herramientas → memoria
├── models/
│   ├── base.py         # interfaz LLMProvider (desacoplada)
│   ├── ollama.py       # cliente HTTP de Ollama
│   ├── fake.py         # proveedor determinista para pruebas
│   └── __init__.py     # fábrica create_provider()
├── tools/
│   ├── registry.py     # catálogo cerrado + ejecución con aprobación y auditoría
│   └── builtin.py      # memoria, contexto, informes, acciones sensibles
├── memory/
│   ├── db.py           # esquema y conexión SQLite
│   └── store.py        # memorias, conversaciones, auditoría de herramientas
├── tests/              # pruebas de config, memoria, herramientas, asistente, Ollama
├── data/               # datos locales (SQLite, secretos) — excluido de git
├── requirements.txt
├── pyproject.toml      # configuración de pytest, ruff y mypy
├── .env.example        # plantilla sin secretos
└── .gitignore
```

Carpetas como `youtube/`, `research/`, `analytics/`, `vision/` y `ui/` se crearán
**cuando tengan código real** (Fases 2–6): no se dejan módulos vacíos.

---

## 7. Cómo funciona un turno

1. Se carga el historial de la sesión desde SQLite (últimos `YACALOX_HISTORY_WINDOW`).
2. Se envía el prompt de sistema + historial + mensaje al proveedor, con los
   esquemas JSON de las herramientas disponibles.
3. Si el modelo pide una herramienta:
   - ¿Es desconocida? → se rechaza y se le informa al modelo (no se ejecuta nada improvisado).
   - ¿Es sensible? → se pregunta al usuario; si se deniega, se le devuelve el motivo.
   - Se ejecuta, se audita en `tool_log` y el resultado vuelve como mensaje `tool`.
4. Se repite hasta que el modelo responda con texto o se alcance el límite de iteraciones.
5. El mensaje final se guarda en `conversations`.

No hay ejecución de shell, ni escritura de archivos arbitrarios, ni subidas,
ediciones ni borrados en YouTube.

---

## 8. Limitaciones conocidas (Fase 1)

- **Sin YouTube todavía**: no hay API key configurada; el asistente no puede
  consultar vídeos, canales ni tendencias y lo dice cuando se le pregunta.
- **Búsqueda léxica** en la memoria: coincide por palabras, no por significado.
  Los embeddings se evaluarán en una fase posterior.
- **Sin interfaz gráfica** aún (Fase 6); por ahora es CLI.
- **Velocidad limitada por el hardware**: ~4 tok/s con `qwen3:1.7b`.
- **La memoria es local y por máquina**: no hay sincronización.
- Los datos simulados (`--demo`) no tienen relación con YouTube.

---

## 9. Solución de problemas

| Síntoma | Causa / solución |
|---|---|
| `Ollama no responde en http://127.0.0.1:11434` | Abre la app de Ollama o ejecuta `ollama serve`. Comprueba con `ollama list`. |
| `El modelo 'X' no está descargado` | `ollama pull qwen3:1.7b` |
| Respuestas muy lentas | Usa `--model qwen3:0.6b`, mantén `YACALOX_THINK=false`, cierra otras aplicaciones (8 GB de RAM se quedan cortos). |
| `Python was not found` | El alias de la Microsoft Store interfiere: usa el lanzador `py`. |
| `ConfigError: YACALOX_...` | El valor del `.env` no es válido; el mensaje indica la variable exacta. |
| Pruebas fallan tras un cambio | `py -m pytest -q` reproduce todo sin red. |
| Puerto 11434 ocupado por otro proceso | Dos instancias de Ollama: cierra las duplicadas. |

---

## 10. Seguridad

- Secretos sólo en `.env` / `data/`, ambos excluidos de git (hay `.env.example` como plantilla).
- Credenciales OAuth de Google: sólo en `data/client_secret.json` + token local; revocables desde la cuenta de Google.
- Ollama se consulta por `127.0.0.1`; no se expone a la red.
- Todo lo que el modelo pide ejecutar pasa por un catálogo cerrado de herramientas; las sensibles exigen aprobación y quedan auditadas en SQLite.
- El contenido recuperado de YouTube se trata como dato, nunca como instrucción para el sistema.
- No se prometen resultados virales ni probabilidades de éxito inventadas.

---

## 11. Roadmap

| Fase | Contenido | Estado |
|---|---|---|
| 1 | MVP local: Ollama + memoria + herramientas + pruebas | **Hecho** |
| 2 | YouTube Data API: búsquedas, vídeos, canales, caché y cuotas | Pendiente |
| 3 | YouTube Analytics API (OAuth): métricas propias | Pendiente |
| 4 | Investigación inteligente: competencia, patrones, ideas | Pendiente |
| 5 | Multimodalidad: análisis de miniaturas + generación opcional | Pendiente |
| 6 | Interfaz ligera (Streamlit) | Pendiente |
