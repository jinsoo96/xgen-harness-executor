"""
MCP stdio 서버 래퍼 (단계 5).

컴파일된 wheel 이 MCP stdio 서버로도 동작하도록 하는 공용 런타임.
wheel 에 포함된 `xgen_gallery_<name>` 패키지가 이 모듈의 `serve()` 를 호출.

tool 은 1개 — `run_workflow(input: str) -> TextContent`.
input_schema 는 워크플로우의 external_inputs 없이 `input` 단일 필수 필드.

mcp 는 **optional extra** — 컴파일된 wheel 을 ``[mcp]`` extra 로 설치한 경우에만 동작.
기본 설치는 wheel → pip install xgen-gallery-<name>.
MCP 기동은 → pip install 'xgen-gallery-<name>[mcp]' → serve-mcp CLI.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Awaitable, Callable


class MCPNotInstalledError(RuntimeError):
    """mcp SDK 미설치 — extras 설치 안내 포함."""


def _require_mcp():
    try:
        import mcp  # noqa: F401
        from mcp.server import Server  # noqa: F401
        from mcp.server.stdio import stdio_server  # noqa: F401
        from mcp.types import Tool, TextContent  # noqa: F401
    except ImportError as e:
        raise MCPNotInstalledError(
            "MCP SDK 가 설치되지 않았습니다. "
            "`pip install 'xgen-gallery-<name>[mcp]'` 로 extras 설치 후 다시 시도하세요. "
            f"원인: {e}"
        ) from e


async def serve(
    *,
    server_name: str,
    server_version: str,
    tool_description: str,
    arun: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
    """stdio MCP 서버 가동.

    Args:
        server_name: MCP server name (갤러리 이름).
        server_version: 서버 버전 (갤러리 버전).
        tool_description: run_workflow 도구 설명.
        arun: 컴파일 산출 패키지의 비동기 실행 함수 (user_input, overrides=...).
    """
    _require_mcp()

    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent

    server = Server(server_name, version=server_version)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="run_workflow",
                description=tool_description or f"Run {server_name} workflow and return final output",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "User input passed to the harness pipeline",
                        },
                        "overrides": {
                            "type": "object",
                            "description": "external_inputs 런타임 덮어쓰기 (env 보다 우선)",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["input"],
                },
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name != "run_workflow":
            raise ValueError(f"unknown tool: {name}")
        user_input = arguments.get("input", "")
        overrides = arguments.get("overrides") or None
        result = await arun(user_input, overrides=overrides)
        text = result.get("final_output", "") or ""
        payload = {
            "final_output": text,
            "iterations": result.get("iterations", 0),
            "usage": result.get("usage", {}),
        }
        return [
            TextContent(type="text", text=text or json.dumps(payload, ensure_ascii=False)),
        ]

    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


def run_blocking(**kwargs: Any) -> int:
    """동기 래퍼 — CLI 에서 호출."""
    try:
        asyncio.run(serve(**kwargs))
    except MCPNotInstalledError as e:
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0
