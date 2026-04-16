"""
MCP Client — xgen-mcp-station HTTP API 클라이언트

MCP 세션의 도구를 발견하고 호출.
xgen-mcp-station의 REST API를 통해 JSON-RPC를 간접 호출.

Endpoints:
- GET  /api/mcp/sessions/{session_id}/tools  → 도구 목록
- POST /api/mcp/mcp-request                  → 도구 호출 (tools/call)
"""

import json
import logging
from typing import Any, Optional

import httpx

from .base import Tool, ToolResult
from ..core.service_registry import get_service_url

logger = logging.getLogger("harness.tools.mcp")


class MCPClient:
    """xgen-mcp-station HTTP 클라이언트"""

    def __init__(self, base_url: str = "", timeout: float = 60.0):
        if not base_url:
            base_url = get_service_url("xgen-mcp-station")
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout, connect=10.0)

    async def list_tools(self, session_id: str) -> list[dict]:
        """세션의 MCP 도구 목록 조회"""
        url = f"{self._base_url}/api/mcp/sessions/{session_id}/tools"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    tools = data.get("tools", data.get("data", {}).get("tools", []))
                    logger.info("[MCP] Session %s: %d tools discovered", session_id, len(tools))
                    return tools
                else:
                    logger.warning("[MCP] list_tools failed: %d %s", resp.status_code, resp.text[:200])
                    return []
        except Exception as e:
            logger.error("[MCP] list_tools error: %s", e)
            return []

    async def call_tool(self, session_id: str, tool_name: str, arguments: dict) -> str:
        """MCP 도구 호출"""
        url = f"{self._base_url}/api/mcp/mcp-request"
        payload = {
            "session_id": session_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    result_data = data.get("data", data.get("result", {}))
                    # MCP 결과 포맷: {"content": [{"type": "text", "text": "..."}]}
                    content = result_data.get("content", [])
                    if isinstance(content, list):
                        texts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                texts.append(block.get("text", ""))
                            elif isinstance(block, str):
                                texts.append(block)
                        return "\n".join(texts) if texts else json.dumps(result_data, ensure_ascii=False)
                    return str(content)
                else:
                    error_msg = f"MCP call failed ({resp.status_code}): {resp.text[:300]}"
                    logger.warning("[MCP] %s", error_msg)
                    return error_msg
        except Exception as e:
            error_msg = f"MCP call error: {e}"
            logger.error("[MCP] %s", error_msg)
            return error_msg

    async def check_session(self, session_id: str) -> bool:
        """세션 존재 여부 확인"""
        url = f"{self._base_url}/api/mcp/sessions/{session_id}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False


class MCPTool(Tool):
    """MCP 세션 도구를 xgen-harness Tool 인터페이스로 래핑"""

    def __init__(self, session_id: str, tool_def: dict, mcp_client: MCPClient):
        self._session_id = session_id
        self._tool_def = tool_def
        self._mcp_client = mcp_client

    @property
    def name(self) -> str:
        return self._tool_def.get("name", "unknown")

    @property
    def description(self) -> str:
        return self._tool_def.get("description", "")

    @property
    def input_schema(self) -> dict:
        return self._tool_def.get("inputSchema", self._tool_def.get("input_schema", {}))

    @property
    def category(self) -> str:
        return f"mcp:{self._session_id[:8]}"

    @property
    def is_read_only(self) -> bool:
        name_lower = self.name.lower()
        write_keywords = {"create", "update", "delete", "write", "send", "post", "put", "remove"}
        return not any(kw in name_lower for kw in write_keywords)

    async def execute(self, input_data: dict) -> ToolResult:
        result = await self._mcp_client.call_tool(self._session_id, self.name, input_data)
        if result.startswith("MCP call failed") or result.startswith("MCP call error"):
            return ToolResult.error(result)
        return ToolResult.success(result)


async def discover_mcp_tools(
    session_ids: list[str],
    mcp_client: Optional[MCPClient] = None,
) -> list[MCPTool]:
    """여러 MCP 세션에서 도구를 발견하고 MCPTool로 래핑"""
    client = mcp_client or MCPClient()
    all_tools: list[MCPTool] = []

    for session_id in session_ids:
        tool_defs = await client.list_tools(session_id)
        for td in tool_defs:
            all_tools.append(MCPTool(session_id, td, client))

    logger.info("[MCP] Total %d tools from %d sessions", len(all_tools), len(session_ids))
    return all_tools
