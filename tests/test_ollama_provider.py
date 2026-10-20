"""Pruebas del cliente Ollama sin red (sesión HTTP simulada)."""

from __future__ import annotations

import json
from typing import cast

import pytest
import requests

from core.errors import ModelNotFoundError, ModelUnavailableError
from models.ollama import OllamaProvider

TOOLS = [
    {
        "type": "function",
        "function": {"name": "memory_search", "description": "busca", "parameters": {"type": "object"}},
    }
]


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse] | None = None, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.posts: list[dict] = []
        self.gets: list[str] = []
        self.closed = False

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else FakeResponse()

    def get(self, url, timeout=None):
        self.gets.append(url)
        if self.error:
            raise self.error
        return self.responses.pop(0) if self.responses else FakeResponse()

    def close(self):
        self.closed = True


def make_provider(session: FakeSession) -> OllamaProvider:
    return OllamaProvider(
        model="qwen3:1.7b",
        base_url="http://127.0.0.1:11434/",
        timeout=30,
        session=cast(requests.Session, session),
    )


def test_request_body_shape():
    session = FakeSession([FakeResponse(payload={"message": {"content": "hola"}, "done_reason": "stop"})])
    provider = make_provider(session)

    provider.complete(
        [{"role": "user", "content": "hola"}],
        tools=TOOLS,
    )

    body = session.posts[0]["json"]
    assert body["model"] == "qwen3:1.7b"
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"]["temperature"] == pytest.approx(0.4)
    assert body["tools"] == TOOLS
    assert body["messages"][0]["content"] == "hola"
    assert session.posts[0]["url"].endswith("/api/chat")
    assert body["model"] == "qwen3:1.7b"


def test_parses_text_response():
    payload = {"message": {"content": "respuesta"}, "model": "qwen3:1.7b", "done_reason": "stop",
               "prompt_eval_count": 10, "eval_count": 5}
    provider = make_provider(FakeSession([FakeResponse(payload=payload)]))
    response = provider.complete([{"role": "user", "content": "x"}])
    assert response.text == "respuesta"
    assert response.tool_calls == []
    assert response.model == "qwen3:1.7b"
    assert response.usage is not None
    assert response.usage.prompt_tokens == 10


def test_parses_tool_calls_with_dict_and_string_arguments():
    payload = {
        "message": {
            "content": "",
            "tool_calls": [
                {"id": "a", "function": {"name": "memory_search", "arguments": {"query": "hola"}}},
                {"function": {"name": "memory_save", "arguments": '{"content": "nota"}'}},
            ],
        },
        "done_reason": "tool_calls",
    }
    provider = make_provider(FakeSession([FakeResponse(payload=payload)]))
    response = provider.complete([{"role": "user", "content": "x"}], tools=TOOLS)

    assert len(response.tool_calls) == 2
    assert response.tool_calls[0].arguments == {"query": "hola"}
    assert response.tool_calls[1].arguments == {"content": "nota"}
    assert response.tool_calls[1].id == "call_1"


def test_tool_call_without_name_is_dropped():
    payload = {"message": {"content": "ok", "tool_calls": [{"function": {"arguments": {}}}]}}
    provider = make_provider(FakeSession([FakeResponse(payload=payload)]))
    response = provider.complete([{"role": "user", "content": "x"}])
    assert response.tool_calls == []
    assert response.text == "ok"


def test_model_not_found_maps_to_model_error():
    provider = make_provider(FakeSession([FakeResponse(status_code=404, payload={"error": "model not found"})]))
    with pytest.raises(ModelNotFoundError) as exc:
        provider.complete([{"role": "user", "content": "x"}])
    assert "ollama pull qwen3:1.7b" in str(exc.value)


def test_error_field_in_body_maps_to_model_error():
    payload = {"error": "model 'qwen3:1.7b' not found, try pulling it first"}
    provider = make_provider(FakeSession([FakeResponse(payload=payload)]))
    with pytest.raises(ModelNotFoundError):
        provider.complete([{"role": "user", "content": "x"}])


def test_generic_error_maps_to_unavailable():
    provider = make_provider(FakeSession([FakeResponse(status_code=500, text="boom")]))
    with pytest.raises(ModelUnavailableError):
        provider.complete([{"role": "user", "content": "x"}])


def test_connection_error_maps_to_unavailable():
    provider = make_provider(FakeSession(error=requests.ConnectionError("rechazada")))
    with pytest.raises(ModelUnavailableError) as exc:
        provider.complete([{"role": "user", "content": "x"}])
    assert "Ollama" in str(exc.value)


def test_timeout_maps_to_unavailable():
    provider = make_provider(FakeSession(error=requests.Timeout("lenta")))
    with pytest.raises(ModelUnavailableError) as exc:
        provider.complete([{"role": "user", "content": "x"}])
    assert "30s" in str(exc.value)


def test_empty_message_is_an_error():
    payload = {"message": {"content": "", "tool_calls": []}}
    provider = make_provider(FakeSession([FakeResponse(payload=payload)]))
    with pytest.raises(ModelUnavailableError):
        provider.complete([{"role": "user", "content": "x"}])


def test_healthcheck_ok_and_fail():
    ok = make_provider(FakeSession([FakeResponse(payload={"models": []})]))
    ok.healthcheck()

    bad = make_provider(FakeSession(error=requests.ConnectionError("down")))
    with pytest.raises(ModelUnavailableError):
        bad.healthcheck()


def test_available_models_lists_names():
    payload = {"models": [{"name": "qwen3:1.7b"}, {"name": "qwen3:0.6b"}]}
    provider = make_provider(FakeSession([FakeResponse(payload=payload)]))
    assert provider.available_models() == ["qwen3:1.7b", "qwen3:0.6b"]


def test_close_closes_session():
    session = FakeSession()
    make_provider(session).close()
    assert session.closed
