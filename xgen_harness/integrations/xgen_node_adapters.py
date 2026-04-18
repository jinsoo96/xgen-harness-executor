"""xgen 특화 NodeAdapter — xgen-workflow 의 모든 노드 카테고리를 tool_def 로.

xgen-workflow `editor/nodes/xgen/` 카테고리 (functionId 기준):
  agents, api_loader, chat_models, document_loaders, file_system,
  arithmetic, mcp, memory, ml, routers, tools

이 모듈은 **tool-like 카테고리** (LLM 이 tool_call 로 호출 가능한) 를 전수
NodeAdapter 로 등록. 각 카테고리별로 파라미터 스키마를 추출해 tool_def 생성.

실행 위임:
  - RAG 계열  → ServiceProvider.documents.search
  - DB/API    → 기존 api_tool/db_tool 어댑터 (라이브러리 빌트인)
  - MCP        → mcp_sessions 경로
  - FileSystem → ServiceProvider.files (ServiceProvider 확장 필요)
  - 기타       → tool_input 자체를 ToolRef 로 저장

새 카테고리 추가 시 아래 dict 에 한 줄, bootstrap 이 알아서 NodeAdapter 등록.
외부 패키지는 entry_points(group="xgen_harness.node_adapters") 로 자체 등록 가능.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..adapters.node_adapters import NodeAdapter, register_node_adapter

if TYPE_CHECKING:
    from ..adapters.resource_registry import ResourceRegistry

logger = logging.getLogger("harness.xgen_node_adapters")

_BOOTSTRAPPED = False


@dataclass
class _XgenNodeRef:
    """ResourceRegistry 의 _tool_executors 에 저장되는 xgen 노드 실행 참조.

    실행 시 ServiceProvider 의 적절한 서비스로 라우팅 (documents/database/files).
    """
    node_id: str
    category: str                 # 'document_loaders' / 'file_system' / ...
    params: dict


def _extract_params(nd: dict) -> dict:
    return {p["id"]: p.get("value") for p in nd.get("parameters", []) if p.get("value") is not None}


def _build_input_schema_from_params(params_def: list[dict], required: list[str] | None = None) -> dict:
    """노드 parameters 정의 → JSON Schema.

    type_hint 매핑: STR→string, INT→integer, FLOAT→number, BOOL→boolean, 그 외 string.
    """
    type_map = {"STR": "string", "INT": "integer", "FLOAT": "number", "BOOL": "boolean"}
    properties: dict = {}
    for p in params_def:
        pid = p.get("id")
        if not pid:
            continue
        t = type_map.get(str(p.get("type", "")).upper(), "string")
        desc = p.get("description") or p.get("label") or ""
        properties[pid] = {"type": t, "description": desc}
    return {
        "type": "object",
        "properties": properties or {"query": {"type": "string", "description": "Input"}},
        "required": required or [],
    }


# ─── 카테고리별 builder ──────────────────────────────────────

def _build_document_loader_tool(node: dict, registry: "ResourceRegistry") -> None:
    """document_loaders 카테고리 (vectordb_retrieval_*, ontology_search, tool_selector, ...) → rag 계열 tool."""
    from ..adapters.resource_registry import ResourceInfo
    nd = node.get("data", {})
    node_id = nd.get("id") or node.get("id", "")
    if not node_id or node_id in registry._tool_executors:
        return
    params = _extract_params(nd)
    collection = params.get("collection_name") or params.get("collection") or ""
    top_k = params.get("top_k", 4)
    tool_name = f"rag_{node_id}"
    desc = params.get("description") or f"Document retrieval on '{collection}' (top_k={top_k})"
    registry._tool_defs.append({
        "name": tool_name,
        "description": desc,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    })
    registry._tool_executors[tool_name] = _XgenNodeRef(
        node_id=node_id, category="document_loaders", params=params,
    )
    registry._tool_infos.append(ResourceInfo(
        resource_type="rag_collection" if collection else "custom_tool",
        name=tool_name, description=desc, source=collection or "documents",
    ))


def _build_file_system_tool(node: dict, registry: "ResourceRegistry") -> None:
    """file_system 카테고리 → file read/write tool."""
    from ..adapters.resource_registry import ResourceInfo
    nd = node.get("data", {})
    node_id = nd.get("id") or node.get("id", "")
    if not node_id or node_id in registry._tool_executors:
        return
    params = _extract_params(nd)
    tool_name = f"fs_{node_id}"
    desc = params.get("description") or f"File system: {node_id}"
    registry._tool_defs.append({
        "name": tool_name,
        "description": desc,
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "Content (write 시)"},
            },
            "required": ["path"],
        },
    })
    registry._tool_executors[tool_name] = _XgenNodeRef(
        node_id=node_id, category="file_system", params=params,
    )
    registry._tool_infos.append(ResourceInfo(
        resource_type="custom_tool", name=tool_name, description=desc, source="filesystem",
    ))


def _build_tools_category_tool(node: dict, registry: "ResourceRegistry") -> None:
    """tools 카테고리 (input_string, print_any, local_cli_tool 등 20여 종) → generic tool."""
    from ..adapters.resource_registry import ResourceInfo
    nd = node.get("data", {})
    node_id = nd.get("id") or node.get("id", "")
    if not node_id or node_id in registry._tool_executors:
        return
    params = _extract_params(nd)
    tool_name = params.get("tool_name") or node_id
    desc = params.get("description") or nd.get("nodeName") or f"Tool: {tool_name}"
    # params_def 에서 input_schema 자동 생성
    schema = _build_input_schema_from_params(nd.get("parameters", []))
    registry._tool_defs.append({
        "name": tool_name,
        "description": desc,
        "input_schema": schema,
    })
    registry._tool_executors[tool_name] = _XgenNodeRef(
        node_id=node_id, category="tools", params=params,
    )
    registry._tool_infos.append(ResourceInfo(
        resource_type="custom_tool", name=tool_name, description=desc, source="xgen-tools",
    ))


def _build_math_tool(node: dict, registry: "ResourceRegistry") -> None:
    """arithmetic 카테고리 → calculator 계열."""
    from ..adapters.resource_registry import ResourceInfo
    nd = node.get("data", {})
    node_id = nd.get("id") or node.get("id", "")
    if not node_id or node_id in registry._tool_executors:
        return
    tool_name = f"math_{node_id}"
    desc = nd.get("nodeName") or f"Arithmetic: {node_id}"
    registry._tool_defs.append({
        "name": tool_name,
        "description": desc,
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"}, "b": {"type": "number"},
                "expression": {"type": "string", "description": "Math expression (optional)"},
            },
        },
    })
    registry._tool_executors[tool_name] = _XgenNodeRef(
        node_id=node_id, category="arithmetic", params=_extract_params(nd),
    )
    registry._tool_infos.append(ResourceInfo(
        resource_type="custom_tool", name=tool_name, description=desc, source="math",
    ))


def _build_ml_tool(node: dict, registry: "ResourceRegistry") -> None:
    """ml 카테고리 → prediction tool."""
    from ..adapters.resource_registry import ResourceInfo
    nd = node.get("data", {})
    node_id = nd.get("id") or node.get("id", "")
    if not node_id or node_id in registry._tool_executors:
        return
    params = _extract_params(nd)
    tool_name = f"ml_{node_id}"
    desc = params.get("description") or nd.get("nodeName") or f"ML: {node_id}"
    registry._tool_defs.append({
        "name": tool_name,
        "description": desc,
        "input_schema": _build_input_schema_from_params(nd.get("parameters", [])),
    })
    registry._tool_executors[tool_name] = _XgenNodeRef(
        node_id=node_id, category="ml", params=params,
    )
    registry._tool_infos.append(ResourceInfo(
        resource_type="custom_tool", name=tool_name, description=desc, source="ml",
    ))


# ─── Bootstrap ───────────────────────────────────────────────

# functionId 카테고리 → builder 매핑. 한 줄 추가 = 새 카테고리 등록.
_XGEN_CATEGORY_ADAPTERS: dict[str, tuple[list[str], callable, str]] = {
    # name: (function_ids 매칭, builder, description)
    "xgen_document_loaders": (
        ["document_loaders"], _build_document_loader_tool,
        "xgen document_loaders (vectordb/ontology/tool_selector)",
    ),
    "xgen_file_system": (
        ["file_system"], _build_file_system_tool,
        "xgen file_system (minio/storage/table_data_mcp)",
    ),
    "xgen_tools": (
        ["tools"], _build_tools_category_tool,
        "xgen tools 카테고리 — generic tool (20+ 노드)",
    ),
    "xgen_arithmetic": (
        ["arithmetic"], _build_math_tool,
        "xgen math/arithmetic",
    ),
    "xgen_ml": (
        ["ml"], _build_ml_tool,
        "xgen ml 예측 도구",
    ),
}


def bootstrap_xgen_node_adapters() -> None:
    """xgen 특화 NodeAdapter 를 ResourceRegistry 의 레지스트리에 등록.

    `XgenAdapter` 모듈 로드 시 자동 호출. 멱등.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    for name, (fids, builder, desc) in _XGEN_CATEGORY_ADAPTERS.items():
        register_node_adapter(NodeAdapter(
            name=name, function_ids=fids, build=builder,
            resource_type="xgen_node", description=desc,
        ))
    logger.info(
        "[xgen-node-adapters] registered %d adapters: %s",
        len(_XGEN_CATEGORY_ADAPTERS), list(_XGEN_CATEGORY_ADAPTERS.keys()),
    )
