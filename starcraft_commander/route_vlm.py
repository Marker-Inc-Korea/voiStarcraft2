"""Default :class:`~starcraft_commander.route_sketch.VlmReader`: the proxy's OpenAI-compatible chat API with an image part.

Model: ``VOI_ROUTE_VLM_MODEL`` (default :data:`DEFAULT_MODEL`).  Key and base URL reuse the realtime upstream config
(``VOI_REALTIME_API_KEY`` | ``MYPROXY_API_KEY`` | ``OPENAI_API_KEY``; ``VOI_REALTIME_BASE_URL``).  The key is only
ever sent in the Authorization header and is scrubbed from every error message.  Any failure raises :class:`VlmError`;
``interpret_sketch`` turns that into an ``ask`` and never executes.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from starcraft_commander import realtime_intent

DEFAULT_MODEL = "gemini-3-flash"
DEFAULT_TIMEOUT_SECONDS = 20.0
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class VlmError(RuntimeError):
    """The VLM call failed or answered something that is not a JSON object."""


class ProxyVlmReader:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., object] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def read(self, image_png: bytes | None, prompt: str, schema: dict) -> dict:
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        if image_png:
            encoded = base64.b64encode(image_png).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": "Reply with exactly one JSON object matching this JSON schema, no prose: "
                    + json.dumps(schema, ensure_ascii=False),
                },
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": realtime_intent.UPSTREAM_USER_AGENT,
            },
        )
        kwargs: dict[str, object] = {"timeout": self.timeout}
        if self.base_url.startswith("https:"):
            kwargs["context"] = realtime_intent._ssl_context()
        try:
            response = self._opener(request, **kwargs)  # type: ignore[misc]
            raw = response.read()  # type: ignore[attr-defined]
            close = getattr(response, "close", None)
            if callable(close):
                close()
        except urllib.error.HTTPError as error:
            detail = self._scrub(error.read()[:200].decode("utf-8", "replace"))
            raise VlmError(f"VLM HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            raise VlmError(f"VLM unreachable: {self._scrub(str(getattr(error, 'reason', error)))}") from None
        try:
            text = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise VlmError("VLM answered a non-chat-completion document") from None
        if isinstance(text, list):  # some gateways return content parts
            text = "".join(str(p.get("text", "")) for p in text if isinstance(p, Mapping))
        return _parse_json_object(str(text))

    def _scrub(self, text: str) -> str:
        return realtime_intent._scrub(text, self._api_key)


def _parse_json_object(text: str) -> dict:
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1)
    try:
        document = json.loads(stripped)
    except ValueError:
        raise VlmError("VLM answer is not JSON") from None
    if not isinstance(document, dict):
        raise VlmError("VLM answer is not a JSON object")
    return document


def reader_from_env(env: Mapping[str, str] | None = None) -> ProxyVlmReader | None:
    """The default reader, or ``None`` when no API key is configured (deterministic fallback then applies)."""

    env = os.environ if env is None else env
    config = realtime_intent.load_upstream_config(env)
    if not config.api_key:
        return None
    return ProxyVlmReader(
        base_url=config.base_url,
        api_key=config.api_key,
        model=str(env.get("VOI_ROUTE_VLM_MODEL", "") or "").strip() or DEFAULT_MODEL,
    )
