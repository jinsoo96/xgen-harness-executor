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

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Capability 자동 발행 — 로드된 자산을 CapabilityRegistry에 등록
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def publish_capabilities(
        self,
        capability_registry=None,
        *,
        overwrite: bool = True,
        namespace_by_type: bool = True,
    ) -> int:
        """로드된 모든 도구를 CapabilitySpec으로 변환 후 레지스트리에 등록.

        - tool_defs + tool_infos를 바탕으로 ParamSpec 리스트 생성
        - tool_factory는 이 ResourceRegistry를 재활용하는 Tool 래퍼 반환
        - RAG 컬렉션도 `retrieval.rag_<name>` capability로 발행

        Returns:
            등록된 capability 개수
        """
        from ..capabilities import CapabilitySpec, ParamSpec, ProviderKind, get_default_registry

        reg = capability_registry or get_default_registry()
        count = 0

        kind_map = {
            "mcp_tool": ProviderKind.MCP_TOOL,
            "api_tool": ProviderKind.API,
            "db_tool": ProviderKind.DB,
            "gallery_tool": ProviderKind.GALLERY,
            "builtin_tool": ProviderKind.BUILTIN,
            "rag_collection": ProviderKind.RAG,
        }

        info_by_name = {info.name: info for info in self._tool_infos}

        for tool_def in self._tool_defs:
            name = tool_def.get("name")
            if not name:
                continue
            info = info_by_name.get(name)
            resource_type = info.resource_type if info else "tool"
            category = resource_type.replace("_tool", "").replace("_collection", "") or "tool"
            cap_name = f"{category}.{name}" if namespace_by_type else name
            description = tool_def.get("description") or (info.description if info else "")

            params = _schema_to_param_specs(tool_def.get("input_schema", {}))
            factory = _make_resource_tool_factory(self, name, description, tool_def.get("input_schema", {}))

            tags = [category, name]
            if info and info.source:
                tags.append(info.source)

            spec = CapabilitySpec(
                name=cap_name,
                category=category,
                description=description,
                tags=[t for t in tags if t],
                aliases=[name],
                params=params,
                provider_kind=kind_map.get(resource_type, ProviderKind.CUSTOM),
                provider_ref=(info.source if info else name),
                tool_factory=factory,
                tool_name=name,
                is_read_only=False if resource_type in ("api_tool", "db_tool") else True,
            )
            reg.register(spec, overwrite=overwrite)
            count += 1

        # RAG 컬렉션별 capability — search_rag를 도구처럼 노출
        for rag_info in self._rag_collections:
            col_name = rag_info.name
            cap_name = f"retrieval.rag_{col_name}"
            tool_name = f"rag_search_{col_name}"

            factory = _make_rag_capability_factory(self, col_name, tool_name, rag_info.description)
            spec = CapabilitySpec(
                name=cap_name,
                category="retrieval",
                description=rag_info.description or f"RAG 검색 — 컬렉션 '{col_name}'",
                tags=["rag", "document", "retrieval", col_name],
                aliases=[col_name, f"rag_{col_name}"],
                params=[
                    ParamSpec(
                        name="query",
                        type_hint="str",
                        description="검색 질의",
                        required=True,
                        source_hint="user_input",
                    ),
                    ParamSpec(
                        name="top_k",
                        type_hint="int",
                        description="결과 청크 개수",
                        required=False,
                        default=5,
                    ),
                ],
                provider_kind=ProviderKind.RAG,
                provider_ref=col_name,
                tool_factory=factory,
                tool_name=tool_name,
            )
            reg.register(spec, overwrite=overwrite)
            count += 1

        logger.info("[Resources] Published %d capabilities to registry", count)
        return count

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
        """DB 도구 실행 — ServiceProvider.database.execute_raw_query 경유."""
        query = tool_input.get("query", "")
        if not query:
            return "Error: 'query' parameter is required"

        db = self._services.database
        if not db:
            return "Error: Database service not available"

        # execute_raw_query 있으면 사용 (표준 경로)
        if hasattr(db, "execute_raw_query"):
            try:
                results = await db.execute_raw_query(query, params=[], limit=100)
                if results:
                    return json.dumps(results, ensure_ascii=False, default=str)[:10000]
                return "Query returned 0 rows"
            except Exception as e:
                return f"Database query error: {e}"

        # 레거시 폴백: find_records("__raw_query__") (구 버전 호환)
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
        """선택된 컬렉션은 항상 등록 — 서비스 호출 실패/인증 실패와 무관하게.

        list_collections()가 성공하면 description 등을 enrich, 실패하거나 빈 응답이면
        selected만으로 fallback 등록.
        """
        # 1. selected를 먼저 등록 (인증 실패 등으로 list 못 가져와도 검색은 가능해야 함)
        loaded_names: set[str] = set()
        for name in selected:
            if not name or name in loaded_names:
                continue
            self._rag_collections.append(ResourceInfo(
                resource_type="rag_collection", name=name,
                description="", source="config",
            ))
            loaded_names.add(name)

        # 2. documents 서비스에서 추가 metadata 가져오기 (옵션, 실패 허용)
        if not self._services.documents:
            return

        try:
            all_collections = await self._services.documents.list_collections()
        except Exception as e:
            logger.warning("[Resources] RAG collections list failed (fallback to selected only): %s", e)
            return

        if not all_collections:
            return

        # 가져온 목록 중 selected에 있는 것의 description enrich + 추가 컬렉션 등록
        by_name = {info.name: info for info in self._rag_collections}
        for col in all_collections:
            name = col.get("name", "") if isinstance(col, dict) else str(col)
            if not name:
                continue
            desc = col.get("description", "") if isinstance(col, dict) else ""
            if name in by_name:
                if desc and not by_name[name].description:
                    by_name[name].description = desc
                    by_name[name].source = "xgen-documents"
            elif not selected:
                # selected 가 비어있을 때만 전체 등록 (over-load 방지)
                self._rag_collections.append(ResourceInfo(
                    resource_type="rag_collection", name=name,
                    description=desc, source="xgen-documents",
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Capability helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_JSON_TYPE_TO_HINT = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list[str]",
    "object": "dict",
}


def _schema_to_param_specs(schema: Any) -> list:
    """JSON Schema → ParamSpec 리스트"""
    from ..capabilities import ParamSpec

    if not isinstance(schema, dict):
        return []
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    params = []
    for pname, prop in props.items():
        if not isinstance(prop, dict):
            continue
        t = prop.get("type", "string")
        type_hint = _JSON_TYPE_TO_HINT.get(t, "str")
        params.append(
            ParamSpec(
                name=pname,
                type_hint=type_hint,
                description=prop.get("description", ""),
                required=pname in required,
                default=prop.get("default"),
                enum=prop.get("enum"),
            )
        )
    return params


def _make_resource_tool_factory(registry: "ResourceRegistry", tool_name: str, description: str, schema: dict):
    """ResourceRegistry의 도구를 Tool 인터페이스로 감싸는 factory 생성"""

    def factory(config: dict) -> Tool:
        return _ResourceToolWrapper(tool_name, description, schema, registry)

    return factory


def _make_rag_capability_factory(registry: "ResourceRegistry", collection: str, tool_name: str, description: str):
    """RAG 컬렉션을 도구처럼 실행하는 factory"""

    def factory(config: dict) -> Tool:
        default_top_k = int(config.get("top_k", 5))
        return _RAGCollectionTool(
            tool_name=tool_name,
            collection=collection,
            description=description,
            registry=registry,
            default_top_k=default_top_k,
        )

    return factory


class _ResourceToolWrapper(Tool):
    """ResourceRegistry.execute_tool 위에 얹힌 Tool 어댑터"""

    def __init__(self, tool_name: str, description: str, schema: dict, registry: "ResourceRegistry"):
        self._name = tool_name
        self._desc = description
        self._schema = schema
        self._registry = registry

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    @property
    def input_schema(self) -> dict:
        return self._schema or {"type": "object", "properties": {}}

    async def execute(self, input_data: dict) -> ToolResult:
        content = await self._registry.execute_tool(self._name, input_data or {})
        if isinstance(content, str) and content.startswith("Error"):
            return ToolResult.error(content)
        return ToolResult.success(content if isinstance(content, str) else str(content))


class _RAGCollectionTool(Tool):
    """RAG 컬렉션별 검색 도구"""

    def __init__(self, tool_name: str, collection: str, description: str, registry: "ResourceRegistry", default_top_k: int = 5):
        self._name = tool_name
        self._collection = collection
        self._desc = description or f"Search RAG collection '{collection}'"
        self._registry = registry
        self._default_top_k = default_top_k

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 질의"},
                "top_k": {"type": "integer", "description": "결과 청크 수"},
            },
            "required": ["query"],
        }

    async def execute(self, input_data: dict) -> ToolResult:
        query = (input_data or {}).get("query", "").strip()
        if not query:
            return ToolResult.error("query is required")
        top_k = int((input_data or {}).get("top_k") or self._default_top_k)
        text = await self._registry.search_rag(query, collections=[self._collection], top_k=top_k)
        return ToolResult.success(text or "(no results)")
