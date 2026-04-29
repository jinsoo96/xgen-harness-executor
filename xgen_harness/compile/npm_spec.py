"""
npm_spec — 하네스 워크플로우의 모든 stage 설정값을 spec.json 으로 정규화.

v0.28+ npm 채널의 핵심. ``xgen-harness-engine-node`` 가 이 spec.json 을
읽어 13 stage / 31 strategy / capability / RAG / 모든 stage_params 그대로 재현.

설계 원칙:
  - **fully equivalent**: HarnessConfig 의 모든 필드가 1:1 으로 spec 에 박힘.
    임시방편적 minimal pipeline X — engine-node 가 같은 동작을 보장.
  - **freeze**: xgen-nodes 같은 외부 코드 의존 도구는 publish 시점에
    input_schema + 호출 메타를 spec 에 freeze (TypeScript 가 직접 호출).
  - **결정적**: 같은 입력 → 같은 spec.json (sorted keys, 파일/시간 의존 X).

스키마 버전 변경 시 ``SPEC_VERSION`` 올림.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .snapshot import WorkflowSnapshot

SPEC_VERSION = "1.0"

# spec.json 의 최상위 키 — TypeScript 측 zod schema 와 1:1.
SPEC_TOP_KEYS = (
    "spec_version",
    "harness_version",
    "gallery_name",
    "gallery_version",
    "compiled_at",
    "config",          # HarnessConfig 전체 — 1:1 dict
    "tool_definitions",  # frozen tool schema (xgen-nodes 등)
    "external_inputs",
    "metadata",
)


@dataclass
class FrozenToolDefinition:
    """publish 시점 freeze 된 도구 스키마.

    xgen-nodes 같은 Python 클래스 의존 도구는 외부 환경에 NodeClass 가 없으므로
    ``execute()`` 결과인 langchain Tool 의 args_schema + 호출 명세를 spec 에 박아
    TypeScript runner 가 직접 외부 API (Tavily/Brave/Naver 등) 호출하도록 한다.

    Fields:
        name: LLM 에 노출할 도구 이름 (sanitized).
        description: 도구 설명.
        input_schema: JSON Schema (LLM tool definition).
        call_kind: "http" | "mcp_session" | "rag" | "noop"
        call_spec: kind 별 호출 스펙. 예) http={url, method, headers, body_template}
                   mcp_session={session_id} (mcp-station proxy)
                   rag={collection_name, top_k, score_threshold}
        annotations: MCP annotations (read_only_hint 등).
        tags: 카테고리 태그.
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    call_kind: str = "noop"
    call_spec: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass
class HarnessSpec:
    """spec.json 의 Python 모델. ``to_json`` → 정규화된 JSON 문자열.

    TypeScript 측 ``src/spec/schema.ts`` 의 zod schema 와 1:1.
    """

    spec_version: str = SPEC_VERSION
    # default 는 dataclass 인스턴스 직접 생성 시에만 사용 (build_spec 경로는
    # snapshot.harness_version 으로 항상 override 함 — npm_spec.py:131 참조).
    # 정확한 값은 컴파일 시점 __version__ 으로 결정 (snapshot._current_harness_spec).
    # 박제 fallback 은 회귀 위험 — 빈 문자열 = "어떤 엔진 버전과도 호환".
    harness_version: str = ""
    gallery_name: str = ""
    gallery_version: str = "0.1.0"
    compiled_at: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)
    external_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self, *, indent: int = 2) -> str:
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, indent=indent, sort_keys=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_spec(
    snapshot: WorkflowSnapshot,
    *,
    tool_definitions: Optional[list[FrozenToolDefinition]] = None,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> HarnessSpec:
    """``WorkflowSnapshot`` → ``HarnessSpec``.

    snapshot 에는 이미 ``harness_config`` 전체 dict 가 포함되어 있어 fully
    equivalent 보장. tool_definitions 는 publish 흐름에서 별도 수집해 전달
    (xgen-nodes resolver 등을 호출해야 하므로 이 함수 책임 밖).

    Args:
        snapshot: WorkflowSnapshot.from_config(...) 산출물.
        tool_definitions: freeze 된 도구 카탈로그. None 이면 빈 배열 — runner
                          가 mcp_session ToolSource 만 사용 (xgen-nodes 미동작).
        extra_metadata: 추가 메타 (publisher 정보 등).
    """
    import time

    config_dict = dict(snapshot.harness_config or {})

    # tool_definitions 정규화 (FrozenToolDefinition → dict)
    td_list: list[dict[str, Any]] = []
    for t in tool_definitions or []:
        if isinstance(t, FrozenToolDefinition):
            td_list.append(asdict(t))
        elif isinstance(t, dict):
            td_list.append(_normalize_tool_def(t))

    metadata = dict(snapshot.metadata or {})
    if extra_metadata:
        metadata.update(extra_metadata)

    return HarnessSpec(
        spec_version=SPEC_VERSION,
        harness_version=snapshot.harness_version,
        gallery_name=snapshot.gallery_name,
        gallery_version=snapshot.gallery_version,
        compiled_at=metadata.pop("compiled_at", "") or _utc_now_iso(),
        config=config_dict,
        tool_definitions=td_list,
        external_inputs=dict(snapshot.external_inputs or {}),
        metadata=metadata,
    )


def _normalize_tool_def(d: dict[str, Any]) -> dict[str, Any]:
    """외부에서 dict 로 전달된 tool def 정규화 — 누락 필드 default."""
    return {
        "name": str(d.get("name") or ""),
        "description": str(d.get("description") or ""),
        "input_schema": d.get("input_schema") or {"type": "object"},
        "call_kind": str(d.get("call_kind") or "noop"),
        "call_spec": d.get("call_spec") or {},
        "annotations": d.get("annotations") or {},
        "tags": list(d.get("tags") or []),
    }


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ─── helpers for ToolSource freezing — publish 흐름에서 사용 ─────
#
# generic helpers — 도메인 무관. 이식측이 자기 도메인 어댑터 (xgen-nodes /
# Slack / Notion 등) 에서 호출. 엔진 코드엔 xgen 도메인 박지 않음.


def freeze_http_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    call_url: str,
    call_method: str = "POST",
    call_headers: Optional[dict[str, str]] = None,
    call_body_template: Optional[dict[str, Any]] = None,
    secrets_keys: Optional[list[str]] = None,
    extra_call_spec: Optional[dict[str, Any]] = None,
) -> FrozenToolDefinition:
    """generic HTTP 호출 도구를 spec freeze 형태로 변환.

    runner 가 이 정의만으로 외부 API 호출 가능 — caller 환경의 NodeClass /
    langchain Tool 의존성 0.

    secrets_keys 는 환경변수에서 가져올 인증값 이름 (Tavily/Brave API key 등).
    spec.json 에 secret 값을 직접 박지 않고 키 이름만 — 외부 환경의 env 가
    채움.

    extra_call_spec — 호출자 도메인 메타 (예: xgen-nodes 의 node_id) 를 그대로
    보존하기 위한 escape hatch. runner 는 이 키 무시.
    """
    spec: dict[str, Any] = {
        "url": call_url,
        "method": call_method,
        "headers": call_headers or {},
        "body_template": call_body_template or {},
        "secrets_keys": list(secrets_keys or []),
    }
    if extra_call_spec:
        spec.update(extra_call_spec)
    return FrozenToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        call_kind="http",
        call_spec=spec,
    )


# alias — 이식측 (xgen-workflow harness_bridge) 가 호출. 엔진 함수는 generic
# 으로 유지하면서 alias 한 줄로 도메인 사용자 편의 제공.
def freeze_xgen_node_tool(
    *,
    node_id: str,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    call_url: str,
    call_method: str = "POST",
    call_headers: Optional[dict[str, str]] = None,
    call_body_template: Optional[dict[str, Any]] = None,
    secrets_keys: Optional[list[str]] = None,
) -> FrozenToolDefinition:
    """xgen-nodes alias — `freeze_http_tool` 의 thin wrapper. node_id 메타 보존."""
    return freeze_http_tool(
        name=name,
        description=description,
        input_schema=input_schema,
        call_url=call_url,
        call_method=call_method,
        call_headers=call_headers,
        call_body_template=call_body_template,
        secrets_keys=secrets_keys,
        extra_call_spec={"node_id": node_id},
    )


def freeze_mcp_session_tool(
    *,
    session_id: str,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    annotations: Optional[dict[str, Any]] = None,
) -> FrozenToolDefinition:
    """mcp-station 의 stdio MCP 도구 — runner 가 station HTTP API 로 proxy 호출.

    publish 환경과 외부 환경 둘 다 mcp-station 이 떠있다고 가정. mcp-station
    URL 은 spec.metadata.station_url 에서 읽음.
    """
    return FrozenToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
        call_kind="mcp_session",
        call_spec={"session_id": session_id},
        annotations=annotations or {},
    )


def freeze_rag_tool(
    *,
    collection_name: str,
    name: str = "rag_search",
    description: str = "Search the configured RAG collection.",
    top_k: int = 4,
    score_threshold: float = 0.0,
) -> FrozenToolDefinition:
    """RAG 컬렉션 검색 도구 — runner 가 spec.metadata.rag_endpoint 로 호출."""
    return FrozenToolDefinition(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
            },
            "required": ["query"],
        },
        call_kind="rag",
        call_spec={
            "collection_name": collection_name,
            "top_k": top_k,
            "score_threshold": score_threshold,
        },
    )
