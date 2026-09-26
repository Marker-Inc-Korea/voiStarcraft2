"""Minimal stdio Model Context Protocol server for voiStarcraft2 tools.

The repository intentionally does not require the third-party MCP SDK. The
server implements the small MCP surface needed by the commander:
``initialize``, ``tools/list``, ``tools/call``, and the custom
``voiStarcraft2/tools/call_many`` composition method. It speaks one JSON-RPC
document per stdin line and writes only JSON-RPC documents to stdout.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from starcraft_commander.unified_command_router import (
    CommanderToolRegistry,
    ToolCall,
    create_command_tool_registry,
)


class MCPProtocolError(ValueError):
    """JSON-RPC/MCP request validation failure."""


class VoiStarcraftMCPServer:
    """JSON-RPC server facade over the unified command tool registry."""

    def __init__(self, registry: CommanderToolRegistry) -> None:
        self.registry = registry

    async def handle(self, request: Mapping[str, object]) -> dict[str, object] | None:
        if request.get("jsonrpc") != "2.0":
            return self._error(request, -32600, "jsonrpc must be '2.0'")
        method = request.get("method")
        if not isinstance(method, str):
            return self._error(request, -32600, "method must be a string")
        request_id = request.get("id")
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            return self._error(request, -32602, "params must be an object")

        if method == "notifications/initialized":
            return None
        if method == "ping":
            return self._result(request_id, {})
        if method == "initialize":
            return self._result(
                request_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "voiStarcraft2",
                        "version": "0.1.0",
                    },
                },
            )
        if method == "tools/list":
            return self._result(
                request_id,
                {"tools": [tool.to_mcp_dict() for tool in self.registry.list_tools()]},
            )
        if method == "tools/call":
            return await self._handle_call(request_id, params)
        if method == "voiStarcraft2/tools/call_many":
            return await self._handle_call_many(request_id, params)
        return self._error(request, -32601, f"method not found: {method}")

    async def _handle_call(
        self,
        request_id: object,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            return self._error({"id": request_id}, -32602, "tool name is required")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            return self._error({"id": request_id}, -32602, "arguments must be an object")
        result = await self.registry.call_async(name, arguments)
        return self._result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            result.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    }
                ],
                "isError": not result.ok,
                "structuredContent": result.to_dict(),
            },
        )

    async def _handle_call_many(
        self,
        request_id: object,
        params: Mapping[str, object],
    ) -> dict[str, object]:
        raw_calls = params.get("calls", ())
        if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
            return self._error({"id": request_id}, -32602, "calls must be a list")
        calls: list[ToolCall] = []
        try:
            for raw_call in raw_calls:
                if isinstance(raw_call, ToolCall):
                    calls.append(raw_call)
                elif isinstance(raw_call, Mapping):
                    calls.append(ToolCall.from_mapping(raw_call))
                else:
                    raise ValueError("each call must be an object")
        except (TypeError, ValueError) as error:
            return self._error({"id": request_id}, -32602, str(error))
        results = await self.registry.call_many_async(calls)
        payload = [result.to_dict() for result in results]
        return self._result(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    }
                ],
                "isError": any(not result.ok for result in results),
                "structuredContent": {"results": payload},
            },
        )

    @staticmethod
    def _result(request_id: object, result: Mapping[str, object]) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}

    @staticmethod
    def _error(
        request: Mapping[str, object],
        code: int,
        message: str,
    ) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": code, "message": message},
        }


async def serve_stdio(
    server: VoiStarcraftMCPServer,
    *,
    input_stream: Any = None,
    output_stream: Any = None,
) -> None:
    """Serve newline-delimited JSON-RPC over stdin/stdout."""

    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    for line in input_stream:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise MCPProtocolError("request must be an object")
            response = await server.handle(request)
        except (MCPProtocolError, TypeError, ValueError, json.JSONDecodeError) as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(error)},
            }
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()


def main() -> int:
    """Run the stdio MCP server with a safe no-runtime tool registry."""

    registry = create_command_tool_registry()
    asyncio.run(serve_stdio(VoiStarcraftMCPServer(registry)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
