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

import functools
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..adapters.node_adapters import NodeAdapter, register_node_adapter

if TYPE_CHECKING:
    from ..adapters.resource_registry import ResourceRegistry

logger = logging.getLogger("harness.xgen_node_adapters")

_BOOTSTRAPPED = False

_POLICY_FILENAME = "node_control_policy.json"


@dataclass
class _XgenNodeRef:
    """ResourceRegistry 의 _tool_executors 에 저장되는 xgen 노드 실행 참조.

    실행 시 ServiceProvider 의 적절한 서비스로 라우팅 (documents/database/files).

    params    — mode == 'manual' 인 파라미터 값 (사용자 입력 + 기본값). LLM 스키마에 **노출 안 됨**.
    spec_id   — 캔버스 Node 클래스 id (= nodeId). get_node_class_by_id() 조회 키.
    control_map — {param_key: {'control': 'manual'|'auto'|'switchable', 'mode': 'manual'|'auto'}}
                  디버깅/UI 피드백용 메타.
    """
    node_id: str                  # 캔버스 인스턴스 id (그래프 내 유일)
    category: str                 # 'document_loaders' / 'file_system' / ...
    params: dict
    spec_id: str = ""
    control_map: dict = field(default_factory=dict)


def _extract_params(nd: dict) -> dict:
    return {p["id"]: p.get("value") for p in nd.get("parameters", []) if p.get("value") is not None}


# ─── control policy 로더 / 리졸버 ────────────────────────────

@functools.lru_cache(maxsize=1)
def _load_control_policy() -> dict:
    """node_control_policy.json 을 라이브러리 리소스에서 lazy 로드.

    파일 없음/파싱 오류는 빈 policy 로 폴백 — 레거시 호환 (global_default 만 적용).
    환경변수 XGEN_HARNESS_NODE_POLICY_PATH 로 override 가능 (테스트/운영 override).
    """
    override = os.environ.get("XGEN_HARNESS_NODE_POLICY_PATH")
    candidates = [Path(override)] if override else []
    candidates.append(Path(__file__).parent / _POLICY_FILENAME)
    for path in candidates:
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("[node-control-policy] loaded from %s", path)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning("[node-control-policy] failed to load %s: %s", path, e)
    logger.info("[node-control-policy] no policy file found — using global default")
    return {}


def reload_control_policy() -> dict:
    """테스트/핫리로드 용. 캐시 비우고 다시 로드."""
    _load_control_policy.cache_clear()
    return _load_control_policy()


def _resolve_control_for_node(
    spec_id: str,
    category: str,
    params_def: list[dict],
) -> dict:
    """노드 한 건에 대해 {param_key: {control, default_mode, auto_hint?, synthetic?, type?}} 맵 생성.

    우선순위: policy.nodes[spec_id].params[pk] > policy.categories[category] > policy.global_default.
    synthetic_auto 는 별도 키로 추가 (params 에 없고 LLM 입력으로만 노출).
    """
    policy = _load_control_policy()
    node_pol = (policy.get("nodes") or {}).get(spec_id) or {}
    cat_pol = (policy.get("categories") or {}).get(category) or {}
    glob = policy.get("global_default") or {"control": "switchable", "default_mode": "manual"}

    resolved: dict = {}
    node_params = (node_pol.get("params") or {})
    for p in params_def:
        pid = p.get("id")
        if not pid:
            continue
        np = node_params.get(pid) or {}
        control = np.get("control") or cat_pol.get("default_control") or glob.get("control", "switchable")
        default_mode = np.get("default_mode") or cat_pol.get("default_mode") or glob.get("default_mode", "manual")
        if control == "auto":
            default_mode = "auto"
        elif control == "manual":
            default_mode = "manual"
        resolved[pid] = {
            "control": control,
            "default_mode": default_mode,
            "auto_hint": np.get("auto_hint"),
        }

    for key, spec in (node_pol.get("synthetic_auto") or {}).items():
        if not isinstance(spec, dict):
            continue
        resolved[key] = {
            "control": "auto",
            "default_mode": "auto",
            "auto_hint": spec.get("auto_hint"),
            "type": spec.get("type", "string"),
            "required": bool(spec.get("required", True)),
            "synthetic": True,
        }

    return resolved


_TYPE_MAP = {"STR": "string", "INT": "integer", "FLOAT": "number", "BOOL": "boolean"}


def _apply_node_overrides(
    spec_id: str,
    category: str,
    params_def: list[dict],
    node_overrides: dict,
    base_params: dict,
) -> tuple[dict, dict, list[str], dict]:
    """파라미터별 mode 결정 + manual 값 / auto schema 분리.

    Returns
    -------
    manual_params : dict
        mode == 'manual' 파라미터의 실행 시 값 ({...base_params, user override value}).
    auto_props : dict
        JSON Schema properties — mode == 'auto' 파라미터 + synthetic_auto 전부.
    auto_required : list[str]
        input_schema.required 후보 (required=True 인 auto 키).
    final_control_map : dict
        {param_key: {'control', 'mode'}} — 디버깅/UI 피드백용.
    """
    ctrl = _resolve_control_for_node(spec_id, category, params_def)
    manual_params: dict = {}
    auto_props: dict = {}
    auto_required: list[str] = []
    final: dict = {}

    overrides = node_overrides or {}

    # 1. 정의된 파라미터
    for p in params_def:
        pid = p.get("id")
        if not pid:
            continue
        meta = ctrl.get(pid, {"control": "switchable", "default_mode": "manual"})
        control = meta["control"]
        ov = overrides.get(pid) if isinstance(overrides.get(pid), dict) else None

        if control == "manual":
            mode = "manual"
        elif control == "auto":
            mode = "auto"
        else:
            # switchable: override > default_mode
            mode = (ov.get("mode") if ov else None) or meta.get("default_mode") or "manual"

        final[pid] = {"control": control, "mode": mode}

        if mode == "manual":
            val = ov.get("value") if ov and "value" in ov else None
            if val is None:
                val = base_params.get(pid)
            if val is None:
                val = p.get("value")
            if val is not None:
                manual_params[pid] = val
        else:
            t = _TYPE_MAP.get(str(p.get("type", "")).upper(), "string")
            desc = meta.get("auto_hint") or p.get("description") or p.get("label") or ""
            auto_props[pid] = {"type": t, "description": desc}
            # 정의된 파라미터의 required 는 JSON Schema 에선 보수적으로 비워둠
            # (원본 parameters.required 를 그대로 쓰면 auto 로 돌린 선택 필드까지 강제됨)

    # 2. synthetic_auto — 정의엔 없지만 LLM 이 채워야 하는 입력
    for key, meta in ctrl.items():
        if key in final:
            continue
        if meta.get("synthetic") and meta.get("control") == "auto":
            auto_props[key] = {
                "type": meta.get("type", "string"),
                "description": meta.get("auto_hint") or "Input",
            }
            if meta.get("required"):
                auto_required.append(key)
            final[key] = {"control": "auto", "mode": "auto"}

    return manual_params, auto_props, auto_required, final


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

def _register_xgen_node_tool(
    registry: "ResourceRegistry",
    *,
    instance_id: str,
    spec_id: str,
    category: str,
    tool_name: str,
    description: str,
    resource_type: str,
    source: str,
    manual_params: dict,
    auto_props: dict,
    auto_required: list[str],
    control_map: dict,
    fallback_schema: dict | None = None,
) -> None:
    """5 개 builder 공통 등록 로직.

    - tool_defs.input_schema : auto_props 만 노출 (manual 값은 LLM 스키마에서 숨김).
    - tool_executors : _XgenNodeRef — dispatch 시 _call_xgen_node 로 라우팅.
    - fallback_schema : auto_props 가 비어있을 때 사용할 최소 스키마 (LLM 호출 가능 상태 유지).
    """
    from ..adapters.resource_registry import ResourceInfo

    if not tool_name:
        return

    if auto_props:
        input_schema = {
            "type": "object",
            "properties": dict(auto_props),
            "required": list(auto_required or []),
        }
    else:
        input_schema = {
            "type": "object",
            "properties": dict(fallback_schema or {"input": {"type": "string", "description": "Free-form input (optional)"}}),
            "required": [],
        }

    registry._tool_defs.append({
        "name": tool_name,
        "description": description,
        "input_schema": input_schema,
    })
    registry._tool_executors[tool_name] = _XgenNodeRef(
        node_id=instance_id,
        category=category,
        params=manual_params,
        spec_id=spec_id,
        control_map=control_map,
    )
    registry._tool_infos.append(ResourceInfo(
        resource_type=resource_type,
        name=tool_name,
        description=description,
        source=source,
    ))


def _unpack_node_for_builder(node: dict, registry: "ResourceRegistry", category: str):
    """builder 공통 전처리. 반환: (instance_id, spec_id, params_def, base, manual, auto_props, auto_req, ctrl)."""
    nd = node.get("data", {})
    instance_id = node.get("id") or nd.get("id", "")
    spec_id = nd.get("id") or ""
    if not instance_id or instance_id in registry._tool_executors:
        return None
    params_def = nd.get("parameters") or []
    base = _extract_params(nd)
    overrides = registry.get_node_overrides().get(instance_id, {}) if hasattr(registry, "get_node_overrides") else {}
    manual, auto_props, auto_required, ctrl = _apply_node_overrides(
        spec_id=spec_id,
        category=category,
        params_def=params_def,
        node_overrides=overrides,
        base_params=base,
    )
    return nd, instance_id, spec_id, params_def, base, manual, auto_props, auto_required, ctrl


def _build_document_loader_tool(node: dict, registry: "ResourceRegistry") -> None:
    """document_loaders (Qdrant / RetrievalToolHard/Light/Light+ / Ontology / ToolSelector) → RAG tool."""
    unpacked = _unpack_node_for_builder(node, registry, "document_loaders")
    if unpacked is None:
        return
    nd, instance_id, spec_id, _, base, manual, auto_props, auto_required, ctrl = unpacked

    # tool_name: manual tool_name (ToolHard/Light 등) > nodeName > "rag_{instance_id}"
    tool_name = (manual.get("tool_name") or base.get("tool_name")
                 or f"rag_{instance_id}")
    collection = manual.get("collection_name") or base.get("collection_name") or ""
    top_k = manual.get("top_k") or base.get("top_k") or 4
    desc = (manual.get("description") or base.get("description")
            or nd.get("nodeName")
            or f"Document retrieval on '{collection}' (top_k={top_k})")

    _register_xgen_node_tool(
        registry, instance_id=instance_id, spec_id=spec_id, category="document_loaders",
        tool_name=tool_name, description=desc,
        resource_type="rag_collection" if collection else "custom_tool",
        source=collection or "documents",
        manual_params=manual, auto_props=auto_props, auto_required=auto_required, control_map=ctrl,
        fallback_schema={"query": {"type": "string", "description": "Search query"}},
    )


def _build_file_system_tool(node: dict, registry: "ResourceRegistry") -> None:
    """file_system (filesystem_storage / table_data_mcp / minio_adapter) → file tool."""
    unpacked = _unpack_node_for_builder(node, registry, "file_system")
    if unpacked is None:
        return
    nd, instance_id, spec_id, _, base, manual, auto_props, auto_required, ctrl = unpacked

    tool_name = manual.get("tool_name") or base.get("tool_name") or f"fs_{instance_id}"
    desc = (manual.get("description") or base.get("description")
            or nd.get("nodeName") or f"File system: {instance_id}")

    _register_xgen_node_tool(
        registry, instance_id=instance_id, spec_id=spec_id, category="file_system",
        tool_name=tool_name, description=desc,
        resource_type="custom_tool", source="filesystem",
        manual_params=manual, auto_props=auto_props, auto_required=auto_required, control_map=ctrl,
        fallback_schema={"path": {"type": "string", "description": "File path"}},
    )


def _build_tools_category_tool(node: dict, registry: "ResourceRegistry") -> None:
    """tools 카테고리 — 20+ 노드 (local_cli_tool, workflow_tool, print_any 등)."""
    unpacked = _unpack_node_for_builder(node, registry, "tools")
    if unpacked is None:
        return
    nd, instance_id, spec_id, _, base, manual, auto_props, auto_required, ctrl = unpacked

    tool_name = manual.get("tool_name") or base.get("tool_name") or instance_id
    desc = (manual.get("description") or base.get("description")
            or nd.get("nodeName") or f"Tool: {tool_name}")

    _register_xgen_node_tool(
        registry, instance_id=instance_id, spec_id=spec_id, category="tools",
        tool_name=tool_name, description=desc,
        resource_type="custom_tool", source="xgen-tools",
        manual_params=manual, auto_props=auto_props, auto_required=auto_required, control_map=ctrl,
    )


def _build_math_tool(node: dict, registry: "ResourceRegistry") -> None:
    """arithmetic — math/add_integers 등. a,b 는 synthetic_auto."""
    unpacked = _unpack_node_for_builder(node, registry, "arithmetic")
    if unpacked is None:
        return
    nd, instance_id, spec_id, _, _, manual, auto_props, auto_required, ctrl = unpacked

    tool_name = f"math_{instance_id}"
    desc = nd.get("nodeName") or f"Arithmetic: {instance_id}"

    _register_xgen_node_tool(
        registry, instance_id=instance_id, spec_id=spec_id, category="arithmetic",
        tool_name=tool_name, description=desc,
        resource_type="custom_tool", source="math",
        manual_params=manual, auto_props=auto_props, auto_required=auto_required, control_map=ctrl,
        fallback_schema={
            "a": {"type": "number", "description": "First operand"},
            "b": {"type": "number", "description": "Second operand"},
        },
    )


def _build_ml_tool(node: dict, registry: "ResourceRegistry") -> None:
    """ml — prediction 노드."""
    unpacked = _unpack_node_for_builder(node, registry, "ml")
    if unpacked is None:
        return
    nd, instance_id, spec_id, _, base, manual, auto_props, auto_required, ctrl = unpacked

    tool_name = manual.get("tool_name") or base.get("tool_name") or f"ml_{instance_id}"
    desc = (manual.get("description") or base.get("description")
            or nd.get("nodeName") or f"ML: {instance_id}")

    _register_xgen_node_tool(
        registry, instance_id=instance_id, spec_id=spec_id, category="ml",
        tool_name=tool_name, description=desc,
        resource_type="custom_tool", source="ml",
        manual_params=manual, auto_props=auto_props, auto_required=auto_required, control_map=ctrl,
        fallback_schema={"record": {"type": "object", "description": "Feature key-value dict"}},
    )


# ─── Bootstrap ───────────────────────────────────────────────

# functionId 카테고리 → builder 매핑. 한 줄 추가 = 새 카테고리 등록.
def _build_generic_metadata_only(node: dict, registry: "ResourceRegistry") -> None:
    """metadata-only 어댑터 — Stage 내부 로직(s02/s00_harness/s09) 이 이미 처리하는 카테고리.

    tool_def 는 등록 안 함. ResourceInfo 만 발행해 *발견됐음* 을 표시.
    이렇게 등록함으로써 /options/__list__ 등에서 카테고리 인식 + Capability 발행 가능.
    """
    from ..adapters.resource_registry import ResourceInfo
    nd = node.get("data", {})
    node_id = nd.get("id") or node.get("id", "")
    if not node_id:
        return
    cat = nd.get("functionId", "")
    registry._tool_infos.append(ResourceInfo(
        resource_type=f"xgen_{cat}", name=node_id,
        description=nd.get("nodeName", ""), source=cat,
    ))


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
    # ── metadata-only (Stage 자체 로직 카테고리 — ResourceInfo 발행만) ──
    # agents/chat_models/memory/routers/interaction 노드 발견 시 capability/UI 가
    # 그 존재를 인지할 수 있게 ResourceInfo 만 등록. tool_def 는 발행 안 함.
    "xgen_agents_meta": (
        ["agents"], _build_generic_metadata_only,
        "agent 노드 메타데이터 (Stage s00_harness 자체가 처리)",
    ),
    "xgen_chat_models_meta": (
        ["chat_models"], _build_generic_metadata_only,
        "chat_model/provider 메타 (Stage s01/s00_harness 의 provider 설정 우선)",
    ),
    "xgen_memory_meta": (
        ["memory"], _build_generic_metadata_only,
        "memory 노드 메타 (Stage s02_history 자체 처리)",
    ),
    "xgen_routers_meta": (
        ["routers"], _build_generic_metadata_only,
        "router 노드 메타 (Stage s09_decide 자체 처리)",
    ),
    "xgen_interaction_meta": (
        ["interaction"], _build_generic_metadata_only,
        "interaction/scenario 노드 메타",
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
