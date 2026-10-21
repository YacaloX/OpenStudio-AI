"""Errores de dominio de YacaloX AI.

Todos los errores del sistema heredan de :class:`YacaloXError` para que la CLI
y la interfaz puedan distinguir fallos esperados de errores de programación.
"""

from __future__ import annotations


class YacaloXError(Exception):
    """Error base del proyecto."""


class ConfigError(YacaloXError):
    """Configuración inválida o ausente; el mensaje debe decir cómo corregirla."""


class ModelError(YacaloXError):
    """Error genérico al contactar un proveedor de modelos."""


class ModelUnavailableError(ModelError):
    """El proveedor no responde (servidor caído, red, proceso detenido)."""


class ModelNotFoundError(ModelError):
    """El modelo solicitado no está disponible en el proveedor."""


class ToolError(YacaloXError):
    """Fallo al ejecutar una herramienta."""


class ToolNotFoundError(ToolError):
    """El modelo pidió una herramienta que no existe: se rechaza, no se inventa."""


class ApprovalDenied(YacaloXError):
    """El usuario denegó la ejecución de una herramienta sensible."""


class YouTubeError(YacaloXError):
    """Error genérico de la integración con YouTube."""


class YouTubeConfigError(YouTubeError):
    """Falta la API key o está mal configurada."""


class YouTubeQuotaError(YouTubeError):
    """Cuota diaria de la API agotada (local o del proyecto de Google Cloud)."""


class YouTubeAPIError(YouTubeError):
    """Respuesta de error de la API de YouTube (4xx/5xx)."""

