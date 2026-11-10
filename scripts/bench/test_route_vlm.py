"""route_vlm.ProxyVlm: request shape (image part, schema, no key in logs) and tolerant JSON extraction.  No network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

V = pytest.importorskip("route_vlm")


def _client(handler):
    return httpx.Client(base_url="https://proxy.test/v1", transport=httpx.MockTransport(handler))


def _resp(content, usage=None, status=200):
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}], "usage": usage or {"total_tokens": 321}})


def test_extract_json_handles_fences_prose_and_nesting():
    assert V.extract_json('{"a": 1}') == {"a": 1}
    assert V.extract_json('```json\n{"a": {"b": [1, 2]}}\n```') == {"a": {"b": [1, 2]}}
    assert V.extract_json('여기 답입니다: {"a": "}", "b": 2} 끝') == {"a": "}", "b": 2}
    with pytest.raises(ValueError):
        V.extract_json("no json here")
    with pytest.raises(ValueError):
        V.extract_json("[1, 2]")


def test_request_has_image_part_schema_and_records_usage():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        return _resp('```json\n{"strokes": []}\n```', {"total_tokens": 777})

    vlm = V.ProxyVlm("claude-haiku-5-5", client=_client(handler), api_key="sk-test")
    out = vlm.read(b"\x89PNGxx", "prompt text", {"type": "object", "properties": {"strokes": {"type": "array"}}})
    assert out == {"strokes": []}
    parts = seen["body"]["messages"][-1]["content"]
    assert any(p["type"] == "image_url" and p["image_url"]["url"].startswith("data:image/png;base64,") for p in parts)
    assert any(p["type"] == "text" and "prompt text" in p["text"] and '"strokes"' in p["text"] for p in parts)  # schema is in the prompt
    assert seen["body"]["model"] == "claude-haiku-5-5" and seen["auth"] == "Bearer sk-test"
    assert vlm.last_tokens == 777 and vlm.last_latency_ms is not None


def test_no_image_sends_text_only():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return _resp('{"ok": true}')

    V.ProxyVlm("m", client=_client(handler), api_key="k").read(None, "p", {})
    content = seen["body"]["messages"][-1]["content"]
    assert isinstance(content, str) or all(p["type"] == "text" for p in content)


def test_http_error_and_garbage_raise_vlm_error():
    vlm = V.ProxyVlm("m", client=_client(lambda r: httpx.Response(400, json={"error": {"message": "image input not supported"}})), api_key="k", retries=0)
    with pytest.raises(V.VlmError, match="400"):
        vlm.read(b"png", "p", {})
    vlm2 = V.ProxyVlm("m", client=_client(lambda r: _resp("sorry, I cannot")), api_key="k", retries=0)
    with pytest.raises(V.VlmError):
        vlm2.read(b"png", "p", {})


def test_retries_on_5xx_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(502, text="bad gateway") if len(calls) < 2 else _resp('{"ok": 1}')

    vlm = V.ProxyVlm("m", client=_client(handler), api_key="k", retries=2, backoff_s=0.0)
    assert vlm.read(None, "p", {}) == {"ok": 1} and len(calls) == 2


def test_error_text_never_contains_the_key():
    vlm = V.ProxyVlm("m", client=_client(lambda r: httpx.Response(401, text="bad key sk-secret-123")), api_key="sk-secret-123", retries=0)
    with pytest.raises(V.VlmError) as e:
        vlm.read(None, "p", {})
    assert "sk-secret-123" not in str(e.value)
