"""route_vlm: OpenAI-compatible chat client (proxy) with an image part; offline via an injected opener."""

from __future__ import annotations

import json
import urllib.error

import pytest

from starcraft_commander import route_vlm


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def close(self):
        pass


def _chat(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


def make(opener, **kw):
    return route_vlm.ProxyVlmReader(
        base_url="https://proxy.example/v1", api_key="sk-secret-123456789", model=kw.pop("model", "gemini-3-flash"),
        timeout=kw.pop("timeout", 5.0), opener=opener, **kw,
    )


def test_request_shape_has_image_part_schema_and_user_agent():
    seen = {}

    def opener(request, timeout=None, context=None):
        seen["url"], seen["headers"] = request.full_url, dict(request.header_items())
        seen["body"], seen["timeout"] = json.loads(request.data), timeout
        return _Resp(_chat('{"final_action": "move"}'))

    out = make(opener).read(b"\x89PNGfake", "PROMPT", {"type": "object", "properties": {}})
    assert out == {"final_action": "move"}
    assert seen["url"] == "https://proxy.example/v1/chat/completions"
    assert seen["headers"]["User-agent"] == "curl/8.7.1"
    assert seen["headers"]["Authorization"] == "Bearer sk-secret-123456789"
    body = seen["body"]
    assert body["model"] == "gemini-3-flash"
    parts = body["messages"][-1]["content"]
    assert {"type": "text", "text": "PROMPT"} in parts
    image = [p for p in parts if p["type"] == "image_url"][0]
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert "response_format" in body and seen["timeout"] == 5.0


def test_no_image_sends_text_only():
    seen = {}

    def opener(request, timeout=None, context=None):
        seen["body"] = json.loads(request.data)
        return _Resp(_chat("{}"))

    make(opener).read(None, "P", {})
    assert all(p["type"] == "text" for p in seen["body"]["messages"][-1]["content"])


def test_fenced_json_is_parsed_and_garbage_raises():
    ok = make(lambda r, **k: _Resp(_chat('```json\n{"a": 1}\n```'))).read(None, "P", {})
    assert ok == {"a": 1}
    with pytest.raises(route_vlm.VlmError):
        make(lambda r, **k: _Resp(_chat("not json at all"))).read(None, "P", {})
    with pytest.raises(route_vlm.VlmError):
        make(lambda r, **k: _Resp(b"<html>")).read(None, "P", {})


def test_http_error_and_timeout_raise_vlm_error_without_leaking_the_key():
    def boom(request, **kw):
        raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, __import__("io").BytesIO(b"echo sk-secret-123456789"))

    with pytest.raises(route_vlm.VlmError) as info:
        make(boom).read(None, "P", {})
    assert "sk-secret-123456789" not in str(info.value)

    def slow(request, **kw):
        raise TimeoutError("t")

    with pytest.raises(route_vlm.VlmError):
        make(slow).read(None, "P", {})


def test_from_env_default_model_and_missing_key(monkeypatch):
    monkeypatch.delenv("VOI_ROUTE_VLM_MODEL", raising=False)
    for name in ("VOI_REALTIME_API_KEY", "MYPROXY_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert route_vlm.reader_from_env({}) is None
    reader = route_vlm.reader_from_env({"MYPROXY_API_KEY": "k"})
    assert reader.model == "gemini-3-flash" and reader.base_url == "https://proxy.nomadamas.org/v1"
    reader = route_vlm.reader_from_env({"MYPROXY_API_KEY": "k", "VOI_ROUTE_VLM_MODEL": "gpt-5.4-mini"})
    assert reader.model == "gpt-5.4-mini"
