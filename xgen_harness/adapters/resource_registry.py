"""
ResourceRegistry — xgen 자산을 하네스 스테이지에 바인딩하는 통합 레지스트리

xgen-workflow에 있는 모든 자산(도구, MCP, RAG, DB, API, Gallery)을
하네스 파이프라인의 각 Stage가 "선택만 하면 끼울 수 있는" 구조.

사용 (XgenAdapter 내부):
    registry = ResourceRegistry(services)
    await registry.load_all(workflow_data, harness_config)

    # s04_tool_index에서:
    state.tool_definitions = registry.get_tool_definitions()
    state.metadata["tool_registry"] = registry.get_tool_executors()

    # s03_system_prompt에서:
    rag_context = await registry.search_rag(query, collections)

    # s08_execute에서:
    result = await registry.execute_tool(tool_name, tool_input)
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..core.services import ServiceProvider, NullServiceProvider
from ..tools.base import Tool, ToolResult

logger = logging.getLogger("harness.resources")


@dataclass
class ResourceInfo:
    """리소스 메타데이터 — UI에서 선택할 때 보여주는 정보"""
    resource_type: str  # "mcp_tool", "api_tool", "db_tool", "rag_collection", "gallery_tool"
    name: str
    description: str = ""
    source: str = ""  # 출처 (MCP 세션명, Gallery 패키지명 등)
    metadata: dict = field(default_factory=dict)


class ResourceRegistry:
    """xgen 자산 통합 레지스트리.

    모든 자산을 한 곳에서 관리하고, 하네스 Stage가 필요할 때 꺼내 쓴다.
    """

    def __init__(self, services: Optional[ServiceProvider] = None):
        self._services = services or NullServiceProvider()
        # 도구 저장소
        self._tool_defs: list[dict] = []           # Anthropic API 포맷
        self._tool_executors: dict[str, Any] = {}   # name → 실행 가능한 객체/함수
        self._tool_infos: list[ResourceInfo] = []   # UI용 메타
        # RAG 컬렉션
        self._rag_collections: list[ResourceInfo] = []
        # 로드 상태
        self._loaded = False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  통합 로드 — 한 번에 모든 xgen 자산 수집
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def load_all(
        self,
        workflow_data: dict = None,
        harness_config: dict = None,
    ) -> "ResourceRegistry":
        """xgen 자산 전체 로드.

        Args:
            workflow_data: 워크플로우 JSON (nodes에서 MCP/도구 추출)
            harness_config: 하네스 설정 (mcp_sessions, rag_collections, tools 등)
        """
        wf = workflow_data or {}
        hc = harness_config or {}

        # 1. MCP 도구
        mcp_sessions = self._collect_mcp_sessions(wf, hc)
        if mcp_sessions:
            await self._load_mcp_tools(mcp_sessions)

        # 2. API 도구 (워크플로우 노드에서 추출)
        self._load_api_tools(wf)

        # 3. Gallery 도구 (pip install된 패키지)
        self._load_gallery_tools(hc.get("gallery_packages", []))

        # 4. RAG 컬렉션
        await self._load_rag_collections(hc.get("rag_collections", []))

        self._loaded = True
        logger.info(
            "[Resources] Loaded: %d tools (%d MCP, %d API, %d gallery), %d RAG collections",
            len(self._tool_defs),
            sum(1 for i in self._tool_infos if i.resource_type == "mcp_tool"),
            sum(1 for i in self._tool_infos if i.resource_type == "api_tool"),
            sum(1 for i in self._tool_infos if i.resource_type == "gallery_tool"),
            len(self._rag_collections),
        )
        return self

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Stage에서 쓰는 인터페이스
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_tool_definitions(self) -> list[dict]:
        """s04_tool_index / s07_llm에서 LLM에 전달할 도구 스키마."""
        return list(self._tool_defs)

    def get_tool_executors(self) -> dict[str, Any]:
        """s08_execute에서 도구 이름으로 실행할 객체 매핑."""
        return dict(self._tool_executors)

    def get_resource_infos(self) -> list[ResourceInfo]:
        """UI에서 도구/리소스 선택 목록."""
        return self._tool_infos + self._rag_collections

    def get_rag_collections(self) -> list[ResourceInfo]:
        """UI에서 RAG 컬렉션 선택 목록."""
        return list(self._rag_collections)

    async def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """도구 실행 — MCP, API, Gallery, 빌트인 전부 통합."""
        executor = self._tool_executors.get(tool_name)
        if executor is None:
            return f"Error: Tool '{tool_name}' not found in registry"

        try:
            if isinstance(executor, Tool):
                result = await executor.execute(tool_input)
                return result.content
            elif isinstance(executor, _MCPToolRef):
                return await self._call_mcp_tool(executor.session_id, tool_name, tool_input)
            elif isinstance(executor, _APIToolRef):
                return await self._call_api_tool(executor.spec, tool_input)
            elif isinstance(executor, _DBToolRef):
                return await self._call_db_tool(executor, tool_input)
            elif callable(executor):
                result = executor(tool_name, tool_input)
                if hasattr(result, '__await__'):
                    result = await result
                if isinstance(result, dict):
                    return str(result.get("content", result))
                return str(result)
            else:
                return f"Error: Unknown executor type for '{tool_name}'"
        except Exception as e:
            return f"Error executing '{tool_name}': {e}"

    async def search_rag(self, query: str, collections: list[str] = None, top_k: int = 5) -> str:
        """RAG 검색 — ServiceProvider.documents 경유."""
        if not self._services.documents:
            return ""

        target_collections = collections or [c.name for c in self._rag_collections]
        if not target_collections:
            return ""

        chunks = []
        for col in target_collections:
            try:
                results = await self._services.documents.search(query, col, limit=top_k)
                for doc in results:
                    if isinstance(doc, dict):
                        content = doc.get("content", doc.get("text", ""))
                        source = doc.get("source", doc.get("metadata", {}).get("source", ""))
                        if content:
                            header = f"[{len(chunks) + 1}]"
                            if source:
                                header += f" ({source})"
                            chunks.append(f"{header}\n{content}")
            except Exception as e:
                logger.warning("[Resources] RAG search '%s' failed: %s", col, e)

        return "\n\n".join(chunks)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  1. MCP 도구 로더
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _load_mcp_tools(self, session_ids: list[str]) -> None:
        mcp = self._services.mcp
        if not mcp:
            # 레거시 폴백
            try:
                from ..tools.mcp_client import discover_mcp_tools
                tools = await discover_mcp_tools(session_ids)
                for tool in tools:
                    self._tool_defs.append(tool.to_api_format())
                    self._tool_executors[tool.name] = tool
                    self._tool_infos.append(ResourceInfo(
                        resource_type="mcp_tool", name=tool.name,
                        description=tool.description, source=f"mcp:{tool._session_id}",
                    ))
            except Exception as e:
                logger.warning("[Resources] Legacy MCP load failed: %s", e)
            return

        for sid in session_ids:
            try:
                tools = await mcp.list_tools(sid)
                for tool in tools:
                    name = tool.get("name", "")
                    if not name or name in self._tool_executors:
                        continue
                    desc = tool.get("description", "")
                    schema = tool.get("inputSchema", tool.get("input_schema", {}))

                    self._tool_defs.append({
                        "name": name,
                        "description": desc,
                        "input_schema": schema,
                    })
                    self._tool_executors[name] = _MCPToolRef(session_id=sid)
                    self._tool_infos.append(ResourceInfo(
                        resource_type="mcp_tool", name=name,
                        description=desc, source=f"mcp:{sid}",
                    ))
                logger.info("[Resources] MCP %s: %d tools", sid, len(tools))
            except Exception as e:
                logger.warning("[Resources] MCP %s failed: %s", sid, e)

    async def _call_mcp_tool(self, session_id: str, tool_name: str, tool_input: dict) -> str:
        mcp = self._services.mcp
        if mcp:
            return await mcp.call_tool(session_id, tool_name, tool_input)
        # 레거시
        from ..tools.mcp_client import MCPClient
        return await MCPClient().call_tool(session_id, tool_name, tool_input)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  2. API 도구 로더 — 워크플로우 노드에서 추출
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _load_api_tools(self, workflow_data: dict) -> None:
        for node in workflow_data.get("nodes", []):
            nd = node.get("data", {})
            func_id = nd.get("functionId", "")

            # API Calling Tool 노드
            if func_id in ("api_calling_tool", "api_tool", "custom_api"):
                params = {p["id"]: p.get("value") for p in nd.get("parameters", []) if p.get("value")}
                tool_name = params.get("tool_name", params.get("name", ""))
                if not tool_name or tool_name in self._tool_executors:
                    continue

                spec = {
                    "api_url": params.get("api_endpoint", params.get("api_url", "")),
                    "api_method": params.get("method", "POST"),
                    "api_body": params.get("request_body", {}),
                    "timeout": params.get("timeout", 30),
                    "response_filter": params.get("response_filter", ""),
                }

                desc = params.get("description", f"API tool: {tool_name}")
                input_schema = params.get("input_schema", {"type": "object", "properties": {}})
                if isinstance(input_schema, str):
                    try:
                        input_schema = json.loads(input_schema)
                    except Exception:
                        input_schema = {"type": "object", "properties": {}}

                self._tool_defs.append({
                    "name": tool_name,
                    "description": desc,
                    "input_schema": input_schema,
                })
                self._tool_executors[tool_name] = _APIToolRef(spec=spec)
                self._tool_infos.append(ResourceInfo(
                    resource_type="api_tool", name=tool_name,
                    description=desc, source=spec.get("api_url", ""),
                ))

            # DB Query 노드 → 도구로 노출
            elif func_id in ("postgresql_query", "oracle_query", "db_query"):
                params = {p["id"]: p.get("value") for p in nd.get("parameters", []) if p.get("value")}
                tool_name = params.get("tool_name", f"db_query_{node.get('id', '')[:8]}")
                if tool_name in self._tool_executors:
                    continue

                desc = params.get("description", f"Database query: {tool_name}")
                self._tool_defs.append({
                    "name": tool_name,
                    "description": desc,
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "SQL query"}},
                        "required": ["query"],
                    },
                })
                # DB 도구 실행은 ServiceProvider.database 경유
                self._tool_executors[tool_name] = _DBToolRef(
                    connection_id=params.get("connection_id", ""),
                    db_type=params.get("db_type", "postgresql"),
                )
                self._tool_infos.append(ResourceInfo(
                    resource_type="db_tool", name=tool_name,
                    description=desc, source=params.get("db_type", "db"),
                ))

    async def _call_api_tool(self, spec: dict, tool_input: dict) -> str:
        import httpx

        url = spec.get("api_url", "")
        method = spec.get("api_method", "POST").upper()
        body = {**spec.get("api_body", {}), **tool_input}
        timeout = spec.get("timeout", 30)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(float(timeout))) as client:
                if method == "GET":
                    resp = await client.get(url, params=body)
                else:
                    resp = await client.post(url, json=body)

                text = resp.text[:10000]
                # response_filter 적용
                rf = spec.get("response_filter", "")
                if rf and resp.status_code == 200:
                    try:
                        data = resp.json()
                        for key in rf.split("."):
                            data = data[key]
                        text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
                    except Exception:
                        pass

                return text if resp.status_code == 200 else f"API error {resp.status_code}: {text[:500]}"
        except Exception as e:
            return f"API call failed: {e}"

    async def _call_db_tool(self, ref: "_DBToolRef", tool_input: dict) -> str:
        """DB 도구 실행 — ServiceProvider.database 경유."""
        query = tool_input.get("query", "")
        if not query:
            return "Error: 'query' parameter is required"

        db = self._services.database
        if not db:
            return "Error: Database service not available"

        try:
            results = await db.find_records(
                table="__raw_query__",
                conditions={"query": query, "connection_id": ref.connection_id, "db_type": ref.db_type},
                limit=100,
            )
            if results:
                return json.dumps(results, ensure_ascii=False, default=str)[:10000]
            return "Query returned 0 rows"
        except Exception as e:
            return f"Database query error: {e}"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  3. Gallery 도구 로더
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _load_gallery_tools(self, packages: list[str]) -> None:
        from ..tools.gallery import load_tool_package, discover_gallery_tools

        # 명시적 패키지
        for pkg in packages:
            tools = load_tool_package(pkg)
            for tool in tools:
                if tool.name in self._tool_executors:
                    continue
                self._tool_defs.append(tool.to_api_format())
                self._tool_executors[tool.name] = tool
                self._tool_infos.append(ResourceInfo(
                    resource_type="gallery_tool", name=tool.name,
                    description=tool.description, source=pkg,
                ))

        # entry_points 자동 발견
        for tool in discover_gallery_tools():
            if tool.name in self._tool_executors:
                continue
            self._tool_defs.append(tool.to_api_format())
            self._tool_executors[tool.name] = tool
            self._tool_infos.append(ResourceInfo(
                resource_type="gallery_tool", name=tool.name,
                description=tool.description, source=tool.category,
            ))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  4. RAG 컬렉션 로더
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _load_rag_collections(self, selected: list[str]) -> None:
        if not self._services.documents:
            # 선택된 컬렉션만 기록 (실제 검색은 search_rag에서)
            for name in selected:
                self._rag_collections.append(ResourceInfo(
                    resource_type="rag_collection", name=name,
                    description="", source="config",
                ))
            return

        try:
            all_collections = await self._services.documents.list_collections()
            for col in all_collections:
                name = col.get("name", "") if isinstance(col, dict) else str(col)
                if not name:
                    continue
                desc = col.get("description", "") if isinstance(col, dict) else ""
                self._rag_collections.append(ResourceInfo(
                    resource_type="rag_collection", name=name,
                    description=desc, source="xgen-documents",
                ))
        except Exception as e:
            logger.warning("[Resources] RAG collections load failed: %s", e)
            for name in selected:
                self._rag_collections.append(ResourceInfo(
                    resource_type="rag_collection", name=name, source="config",
                ))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  헬퍼
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _collect_mcp_sessions(self, workflow_data: dict, hc: dict) -> list[str]:
        sessions = []
        for node in workflow_data.get("nodes", []):
            nd = node.get("data", {})
            for p in nd.get("parameters", []) or []:
                if p.get("id") in ("mcp_session_id", "session_id") and p.get("value"):
                    sid = str(p["value"]).strip()
                    if sid and sid not in sessions:
                        sessions.append(sid)
        for sid in hc.get("mcp_sessions", []):
            if sid and sid not in sessions:
                sessions.append(sid)
        return sessions


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  내부 참조 타입 — 도구 실행 방식을 결정하는 마커
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class _MCPToolRef:
    session_id: str

@dataclass
class _APIToolRef:
    spec: dict

@dataclass
class _DBToolRef:
    connection_id: str
    db_type: str = "postgresql"
