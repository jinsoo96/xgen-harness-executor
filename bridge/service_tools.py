"""
XGEN Service Tools MCP Bridge
===============================
기존 xgen 서비스(documents, mcp-station)의 API를 MCP 도구로 래핑.
Python 노드 import 없이 HTTP API 직접 호출.

Rust 하네스 실행기가 subprocess로 spawn → stdio MCP 통신.
"""

import asyncio
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.error


DOCUMENTS_URL = os.environ.get("DOCUMENTS_SERVICE_BASE_URL", "http://xgen-documents:8000")
MCP_STATION_URL = os.environ.get("MCP_STATION_BASE_URL", "http://xgen-mcp-station:8000")


# =============================================================================
# MCP 서버
# =============================================================================

class ServiceToolsMcpServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self):
        self.tools: Dict[str, "ServiceTool"] = {}
        self.initialized = False

    def register(self, tool: "ServiceTool"):
        self.tools[tool.name] = tool

    async def handle_request(self, request: Dict) -> Optional[Dict]:
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        if req_id is None:
            if method == "notifications/initialized":
                self.initialized = True
            return None

        if method == "initialize":
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "xgen-service-tools", "version": "0.1.0"},
            }}

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "tools": [t.to_mcp_def() for t in self.tools.values()]
            }}

        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            tool = self.tools.get(name)
            if not tool:
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": f"Tool not found: {name}"}], "isError": True}}
            try:
                result = await tool.call(args)
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": result}], "isError": False}}
            except Exception as e:
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}}

        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown: {method}"}}


class ServiceTool:
    def __init__(self, name: str, description: str, schema: Dict, handler):
        self.name = name
        self.description = description
        self.schema = schema
        self.handler = handler

    def to_mcp_def(self) -> Dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}

    async def call(self, args: Dict) -> str:
        return await self.handler(args)


# =============================================================================
# 서비스 도구 정의
# =============================================================================

def _http_get(url: str, headers: dict = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def _http_post(url: str, body: dict, headers: dict = None) -> dict:
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


async def search_documents(args: Dict) -> str:
    """xgen-documents 벡터 검색"""
    query = args.get("query", "")
    collection = args.get("collection_name", "")
    limit = args.get("limit", 5)
    user_id = str(args.get("user_id", "1"))

    if not query:
        return "Error: query is required"

    headers = {"x-user-id": user_id, "x-user-name": "harness"}

    if not collection:
        collections = _http_get(f"{DOCUMENTS_URL}/api/retrieval/collections", headers)
        if isinstance(collections, list) and collections:
            collection = collections[0].get("collection_name", "")
        if not collection:
            return "No collections available"

    data = _http_post(f"{DOCUMENTS_URL}/api/retrieval/documents/search",
                      {"collection_name": collection, "query_text": query,
                       "limit": limit, "score_threshold": 0.1}, headers)
    results = data.get("results", [])
    if not results:
        return f"No results found for: {query}"

    output = []
    for i, r in enumerate(results):
        text = r.get("chunk_text", "")
        score = r.get("score", 0)
        fname = r.get("file_name", "")
        output.append(f"[{i+1}] (score: {score:.3f}, file: {fname})\n{text}")
    return "\n\n---\n\n".join(output)


async def list_collections(args: Dict) -> str:
    """사용 가능한 문서 컬렉션 목록"""
    user_id = str(args.get("user_id", "1"))
    collections = _http_get(f"{DOCUMENTS_URL}/api/retrieval/collections",
                            {"x-user-id": user_id, "x-user-name": "harness"})
    if not isinstance(collections, list):
        return json.dumps(collections, ensure_ascii=False)
    lines = []
    for c in collections:
        name = c.get("collection_make_name", "")
        cname = c.get("collection_name", "")
        desc = c.get("description", "")
        docs = c.get("total_documents", 0)
        chunks = c.get("total_chunks", 0)
        lines.append(f"- {name} ({cname}): {desc} — {docs} documents, {chunks} chunks")
    return "\n".join(lines) if lines else "No collections"


async def search_in_collection(args: Dict) -> str:
    """특정 컬렉션에서 검색"""
    collection = args.get("collection_name", "")
    query = args.get("query", "")
    limit = args.get("limit", 10)
    user_id = str(args.get("user_id", "1"))

    if not collection or not query:
        return "Error: collection_name and query are required"

    try:
        data = _http_post(f"{DOCUMENTS_URL}/api/retrieval/documents/search",
                          {"collection_name": collection, "query_text": query,
                           "limit": limit, "score_threshold": 0.01},
                          {"x-user-id": user_id, "x-user-name": "harness"})
    except Exception as e:
        return f"Search API error: {e}"

    results = data.get("results", [])
    if not results:
        return f"No results in {collection} for: {query}"

    output = []
    for i, r in enumerate(results):
        text = r.get("chunk_text", "")
        score = r.get("score", 0)
        fname = r.get("file_name", "")
        output.append(f"[{i+1}] score={score:.3f} | file={fname}\n{text}")
    return "\n\n---\n\n".join(output)


# =============================================================================
# 서버 설정 + stdio
# =============================================================================

def create_server() -> ServiceToolsMcpServer:
    server = ServiceToolsMcpServer()

    server.register(ServiceTool(
        name="search_documents",
        description="문서를 검색합니다. 사용자 질문에 관련된 문서를 벡터 검색으로 찾습니다.",
        schema={"type": "object", "properties": {
            "query": {"type": "string", "description": "검색할 질문 또는 키워드"},
            "collection_name": {"type": "string", "description": "검색할 컬렉션 이름 (미지정 시 첫 번째 컬렉션)"},
            "limit": {"type": "integer", "description": "검색 결과 수", "default": 5},
        }, "required": ["query"]},
        handler=search_documents,
    ))

    server.register(ServiceTool(
        name="list_collections",
        description="사용 가능한 문서 컬렉션 목록을 조회합니다. 어떤 문서가 있는지 확인할 때 사용합니다.",
        schema={"type": "object", "properties": {}},
        handler=list_collections,
    ))

    server.register(ServiceTool(
        name="search_in_collection",
        description="특정 컬렉션에서 문서를 검색합니다. collection_name을 알고 있을 때 사용합니다.",
        schema={"type": "object", "properties": {
            "collection_name": {"type": "string", "description": "컬렉션 이름 (list_collections로 확인)"},
            "query": {"type": "string", "description": "검색 질문"},
            "limit": {"type": "integer", "description": "결과 수", "default": 10},
        }, "required": ["collection_name", "query"]},
        handler=search_in_collection,
    ))

    return server


async def run_stdio(server: ServiceToolsMcpServer):
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    wt, wp = await asyncio.get_event_loop().connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
    writer = asyncio.StreamWriter(wt, wp, reader, asyncio.get_event_loop())

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            req = json.loads(line.decode().strip())
        except json.JSONDecodeError:
            continue
        resp = await server.handle_request(req)
        if resp is not None:
            writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
            await writer.drain()


if __name__ == "__main__":
    server = create_server()
    print(f"xgen-service-tools: {len(server.tools)} tools ready", file=sys.stderr)
    asyncio.run(run_stdio(server))
