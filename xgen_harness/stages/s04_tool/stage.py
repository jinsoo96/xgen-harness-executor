"""
S04 Tool Index — Progressive 도구 디스커버리 (v0.25.0 재설계)

## 책임
- 등록된 모든 ``ToolSource`` 를 순회하여 도구 정의를 수집한다.
- 사용자가 UI 에서 고른 필터 / 도구 화이트리스트를 적용한다.
- Strategy (progressive_3level / eager_load / none) 으로 LLM 프롬프트용 색인을 만든다.
- RAG 컬렉션 / capability / builtin 도구를 별도 경로로 병합한다.

## 변경점 (v0.24 → v0.25)
- ``mcp_sessions`` / ``custom_tools`` / ``node_tags`` / ``cli_skills`` stage_param **제거**.
- 단일 stage_param ``selected_tools: dict[str, list[str]]`` (source_id → 허용 도구 이름).
  - 키 없음 = 해당 소스의 모든 도구 포함.
  - 빈 리스트 = 해당 소스 비활성.
  - 이름 리스트 = 그 이름만 포함.
- 단일 stage_param ``tool_source_filters: dict[str, dict]`` (source_id → list_tools filter).
  - 각 ToolSource 는 자기 ``filter_schema`` 를 선언하고, 프론트가 sub-UI 로 렌더한다.
  - 예: MCP 세션 소스는 ``session_ids`` 필터, xgen 노드 소스는 ``tags`` 필터.
- MCP/Custom/xgenNode 특수 분기 전부 이식측 ToolSource 구현으로 이관 (엔진 무지식).

## Progressive Disclosure (3 레벨)
- L1: ``state.tool_index`` (이름/설명만) → 시스템 프롬프트 삽입
- L2: ``discover_tools`` 빌트인 도구로 상세 스키마 조회
- L3: 실제 실행 (s07_act)
"""

from __future__ import annotations

import logging
from typing import Optional

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState
from ...events.types import StageSubstepEvent
from ...tools import get_tool_sources
from ...tools.rag_tool import RAGSearchTool

logger = logging.getLogger("harness.stage.tool_index")

# ─── Capability Discovery 기본값 (박제 풀기, v1.0 흡수 from 구 s05_strategy) ───
# 자연어 intent 로 capability 자동 발견 시 사용. stage_params 또는
# register_capability_discovery_defaults() 로 override 가능.
CAPABILITY_DISCOVERY_DEFAULTS: dict[str, float | int] = {
    "top_k": 3,
    "min_score": 0.4,
}


def register_capability_discovery_defaults(*, top_k: int | None = None,
                                            min_score: float | None = None) -> None:
    """capability discovery 기본 임계값 override. 외부 작업자가 자기 도메인에 맞춰 조정."""
    if top_k is not None:
        CAPABILITY_DISCOVERY_DEFAULTS["top_k"] = int(top_k)
    if min_score is not None:
        CAPABILITY_DISCOVERY_DEFAULTS["min_score"] = float(min_score)


class ToolIndexStage(Stage):
    """도구 색인 + progressive disclosure 설정 (v0.25.0)."""

    when_to_use = "도구/RAG/capability/MCP 세션 중 하나라도 쓰려는 요청"
    when_to_skip = "순수 텍스트 답변만 — 외부 호출 0"
    cost_hint = "low"

    @property
    def stage_id(self) -> str:
        return "s04_tool"

    @property
    def order(self) -> int:
        return 4

    async def execute(self, state: PipelineState) -> dict:
        # v0.26.0 — strategy="none" 분기 (D6 fix).
        # 사용자가 도구 인덱싱을 명시적으로 비활성. should_bypass 의 자동 감지와 달리
        # 도구·RAG·capability 가 *있어도* 강제 skip. 디버깅·도구 무관 단발 답변에 유용.
        strategy_name = (self.get_param("strategy", state, None) or "").strip().lower()
        if strategy_name == "none":
            logger.info("[Tool Index] strategy=none, discovery skipped")
            return {"strategy": "none", "tools_indexed": 0, "definitions_bound": 0}

        # ─── stage_params ──────────────────────────────────────────────
        # selected_tools 입력 형태 두 가지 모두 허용:
        #   1) dict {source_id: [tool_name, ...]}  — source 별 화이트리스트
        #   2) list [tool_name, ...]               — 글로벌 화이트리스트 (모든 source)
        # 사유: 사용자가 "이 도구 1개만 쓰고 싶다" 할 때 source_id 를 알 필요 없게.
        # 이전 버전은 list 받으면 .get(sid) 호출에서 AttributeError → silently fallback
        # → 모든 도구 노출 → 비용 폭증 (BUG-B 실측: selected=[tavily] 인데 brave 도 호출).
        _sel_raw = self.get_param("selected_tools", state, {})
        selected_tools_by_source: dict[str, list[str]] = {}
        global_allow: Optional[set[str]] = None
        if isinstance(_sel_raw, dict):
            selected_tools_by_source = _sel_raw
        elif isinstance(_sel_raw, list):
            global_allow = {str(n) for n in _sel_raw if n}
        # 그 외 타입은 무시 (전부 허용으로 폴백).

        tool_source_filters: dict[str, dict] = (
            self.get_param("tool_source_filters", state, {}) or {}
        )
        rag_collections: list[str] = self.get_param("rag_collections", state, []) or []
        rag_top_k: int = self.get_param("rag_top_k", state, None) or 0

        # ─── 0.5 Capability 바인딩 ────────────────────────────────────
        # (a) config.capabilities 명시 선언 → materialize
        cap_result = self._bind_capabilities(state)
        if cap_result.get("_events"):
            from ...events.types import CapabilityBindEvent
            for ev in cap_result["_events"]:
                await state.emit_verbose(CapabilityBindEvent(
                    name=ev["name"], source=ev["source"], stage_id=self.stage_id,
                ))

        # (b) v1.0 — 자연어 intent → capability 자동 발견 (구 s05_strategy 흡수).
        # active_strategy=="capability_auto" 이거나 stage_params.capability_discovery=True 일 때.
        if self._is_capability_discovery_enabled(state):
            disc_result = await self._discover_and_bind_capabilities(state)
            cap_result["discovery"] = disc_result

        # ─── 1. ToolSource 수집 (단일 공급 채널) ──────────────────────
        existing_names = {td.get("name") for td in state.tool_definitions}
        source_tool_map: dict[str, str] = state.metadata.setdefault("tool_source_of", {})
        collected = 0
        sources_used: list[str] = []

        sources = get_tool_sources()
        await state.emit_verbose(StageSubstepEvent(
            stage_id=self.stage_id, substep="sources_discover_start",
            meta={"source_count": len(sources)},
        ))

        for src in sources:
            sid = getattr(src, "source_id", None) or type(src).__name__
            allow = selected_tools_by_source.get(sid)
            # 빈 리스트 = 명시적으로 비활성
            if allow is not None and len(allow) == 0:
                continue
            filter_params = tool_source_filters.get(sid)

            try:
                listed = await self._invoke_list_tools(src, filter_params)
            except Exception as e:
                logger.debug("[Tool Index] list_tools failed for %s: %s", sid, e)
                continue

            before = collected
            for t in listed or []:
                if not isinstance(t, dict):
                    continue
                nm = t.get("name")
                if not nm or nm in existing_names:
                    continue
                if allow is not None and nm not in allow:
                    continue
                if global_allow is not None and nm not in global_allow:
                    continue
                td = {
                    "name": nm,
                    "description": t.get("description", "") or "",
                    "input_schema": t.get("input_schema") or {"type": "object"},
                }
                # annotations 는 payload 분리 (v0.24.4 — Anthropic 400 방지)
                ann = t.get("annotations")
                if ann:
                    state.tool.annotations[nm] = dict(ann)
                # tags 는 metadata 로 보존 (Planner catalog / UI 필터용)
                tags = t.get("tags")
                if tags:
                    td.setdefault("metadata", {})["tags"] = list(tags)
                state.tool_definitions.append(td)
                source_tool_map[nm] = sid
                existing_names.add(nm)
                collected += 1
            if collected > before:
                sources_used.append(sid)

        await state.emit_verbose(StageSubstepEvent(
            stage_id=self.stage_id, substep="sources_discover_complete",
            meta={"tools_collected": collected, "sources_used": sources_used},
        ))

        if collected:
            logger.info("[Tool Index] tool_sources: %d tool(s) from %d source(s)",
                        collected, len(sources_used))

        # ─── 2. Strategy 디스패치 (progressive_3level / eager_load / none) ─
        selected_builtins: list[str] = self.get_param(
            "builtin_tools", state, ["discover_tools"],
        )
        strategy = self.resolve_strategy("discovery", state, "progressive_3level")
        if not strategy:
            from ..strategies.discovery import ProgressiveDiscovery
            strategy = ProgressiveDiscovery()
        tool_index, augmented_defs = await strategy.discover(
            state.tool_definitions, state,
        )

        # discover_tools 빌트인은 선택된 경우만 유지
        if "discover_tools" not in selected_builtins:
            augmented_defs = [td for td in augmented_defs if td.get("name") != "discover_tools"]
            tool_index = [ti for ti in tool_index if ti.get("name") != "discover_tools"]
            logger.info("[Tool Index] discover_tools excluded (not in builtin_tools)")

        # v1.1.1 — fetch_pd 는 strategy 무관 항상 등록.
        #   PD progressive (s06_context.rag_pd_mode='progressive' / s07 tool_result preview) 가
        #   default ON 인 환경에서 LLM 이 lazy fetch 못 하면 정보 누락. 사용자가 도구 화이트리스트
        #   비우거나 EagerLoadDiscovery 골라도 fetch_pd 만은 보장. 이전엔 ProgressiveDiscovery
        #   안에서만 등록해 strategy 갈음 시 누락되던 회귀.
        from ...tools.builtin import FetchPDTool
        if not any(td.get("name") == "fetch_pd" for td in augmented_defs):
            fetch_pd = FetchPDTool(state)
            augmented_defs.append(fetch_pd.to_api_format())
            tool_index.append({
                "name": fetch_pd.name,
                "description": (fetch_pd.description[:120] if hasattr(fetch_pd, "description") else ""),
                "category": "system",
            })
            if hasattr(state, "metadata"):
                state.metadata.setdefault("tool_registry", {})["fetch_pd"] = fetch_pd
            logger.info("[Tool Index] fetch_pd builtin registered (strategy-agnostic)")

        state.tool_definitions = augmented_defs
        state.tool_index = tool_index

        # ─── 3. RAG 설정 (ToolSource 가 아닌 별도 경로) ───────────────
        if rag_collections:
            state.metadata["rag_collections"] = rag_collections
            state.metadata["rag_top_k"] = rag_top_k
            logger.info("[Tool Index] RAG collections: %s (top_k=%d)",
                        rag_collections, rag_top_k)

            rag_tool_mode: str = self.get_param("rag_tool_mode", state, "both")
            if rag_tool_mode in ("tool", "both"):
                _services = state.metadata.get("services")
                _doc_service = getattr(_services, "documents", None) if _services else None
                rag_tool = RAGSearchTool(
                    collections=rag_collections,
                    default_top_k=rag_top_k,
                    doc_service=_doc_service,
                )
                if not any(td.get("name") == "rag_search" for td in state.tool_definitions):
                    state.tool_definitions.append(rag_tool.to_api_format())
                    tool_index.append(rag_tool.to_index_entry())
                    state.metadata.setdefault("tool_registry", {})["rag_search"] = rag_tool
                    logger.info("[Tool Index] rag_search tool registered (mode=%s)",
                                rag_tool_mode)

        # ─── 4. force_tool_use (v0.11.19) ──────────────────────────────
        force_tool_use = bool(self.get_param("force_tool_use", state, False))
        if force_tool_use and state.tool_definitions:
            state.metadata["force_tool_choice"] = "required"
            logger.info("[Tool Index] force_tool_use=True → tool_choice=required")

        logger.info("[Tool Index] %d tools indexed, %d definitions bound",
                    len(tool_index), len(state.tool_definitions))
        return {
            "tools_count": len(tool_index),
            "tools_bound": len(state.tool_definitions),
            "sources_used": sources_used,
            "rag_collections": len(rag_collections),
            "capabilities_declared": cap_result.get("declared", 0),
            "capabilities_resolved": cap_result.get("resolved", 0),
            "capabilities_unknown": cap_result.get("unknown", 0),
            "force_tool_use": force_tool_use,
        }

    # ─── helpers ────────────────────────────────────────────────────────

    @staticmethod
    async def _invoke_list_tools(src, filter_params):
        """``ToolSource.list_tools`` 호출 — filters 인자 미지원 소스 호환."""
        if filter_params is None:
            try:
                return await src.list_tools()
            except TypeError:
                return await src.list_tools(None)
        try:
            return await src.list_tools(filter_params)
        except TypeError:
            # 구형 소스 — filters 무시
            return await src.list_tools()

    def _bind_capabilities(self, state: PipelineState) -> dict:
        """config.capabilities 를 Tool 인스턴스로 materialize 후 state 반영."""
        config = state.config
        if config is None or not getattr(config, "capabilities", None):
            return {"declared": 0, "resolved": 0, "unknown": 0}

        from ...capabilities import materialize_capabilities, merge_into_state

        declared_names = list(config.capabilities)
        report = materialize_capabilities(
            declared_names,
            capability_params=getattr(config, "capability_params", None),
        )
        added = merge_into_state(report, state)

        logger.info(
            "[Tool Index] capabilities: declared=%d, resolved=%d, added=%d, "
            "unknown=%d, no_factory=%d",
            len(declared_names), len(report.resolved), added,
            len(report.unknown), len(report.no_factory),
        )
        if report.unknown:
            logger.warning("[Tool Index] unknown capabilities: %s", report.unknown)
        if report.no_factory:
            logger.warning("[Tool Index] capabilities without tool_factory: %s",
                           report.no_factory)

        return {
            "declared": len(declared_names),
            "resolved": len(report.resolved),
            "unknown": len(report.unknown),
            "_events": [
                {"name": n, "source": "declaration"} for n in report.resolved
            ],
        }

    # ---------- Capability Auto-Discovery (v1.0 흡수 from 구 s05_strategy) ----------

    def _is_capability_discovery_enabled(self, state: PipelineState) -> bool:
        """active_strategy=="capability_auto" 또는 stage_params.capability_discovery=True 면 활성."""
        active = ""
        if state.config:
            picked = (state.config.active_strategies or {}).get(self.stage_id)
            if isinstance(picked, str):
                active = picked.strip()
        if active == "capability_auto":
            return True
        return bool(self.get_param("capability_discovery", state, False))

    async def _discover_and_bind_capabilities(self, state: PipelineState) -> dict:
        """자연어 intent(user_input) → capability 후보 매칭 + state 바인딩.

        - 이미 config.capabilities 에 선언된 것은 _bind_capabilities 가 처리 → 중복 회피
        - 매칭된 것 중 아직 안 된 것만 materialize
        - 임계값(top_k / min_score) 은 stage_params 또는 모듈 상수로 override
        """
        if state.config is None:
            return {"suggestions": 0, "bound": 0}

        intent = state.user_input or ""
        if not intent.strip():
            return {"suggestions": 0, "bound": 0}

        from ...capabilities import (
            CapabilityMatcher,
            MatchStrategy,
            get_default_registry,
            materialize_capabilities,
            merge_into_state,
        )

        already_bound = set(state.metadata.get("capability_bindings", {}).keys())
        already_declared = set(getattr(state.config, "capabilities", []) or [])
        skip = already_bound | already_declared

        top_k = int(self.get_param(
            "capability_top_k", state, CAPABILITY_DISCOVERY_DEFAULTS["top_k"]))
        min_score = float(self.get_param(
            "capability_min_score", state, CAPABILITY_DISCOVERY_DEFAULTS["min_score"]))

        registry = get_default_registry()
        matcher = CapabilityMatcher(registry, min_score=min_score)
        matches = matcher.match(intent, limit=top_k * 2, strategy=MatchStrategy.AUTO)

        suggested = [m for m in matches if m.spec.name not in skip][:top_k]
        if not suggested:
            logger.info("[Tool Index] capability discovery: no new matches (intent=%r)", intent[:80])
            return {"suggestions": 0, "bound": 0}

        names = [m.spec.name for m in suggested]
        state.metadata.setdefault("suggested_capabilities", []).extend(
            [{"name": m.spec.name, "score": m.score, "strategy": m.strategy} for m in suggested]
        )

        report = materialize_capabilities(
            names,
            registry=registry,
            capability_params=getattr(state.config, "capability_params", None),
        )
        added = merge_into_state(report, state)

        logger.info(
            "[Tool Index] capability discovery: suggestions=%s, bound=%d, unknown=%d, no_factory=%d",
            names, added, len(report.unknown), len(report.no_factory),
        )

        from ...events.types import CapabilityBindEvent
        for m in suggested:
            if m.spec.name in report.resolved:
                await state.emit_verbose(CapabilityBindEvent(
                    name=m.spec.name, source="discovery", stage_id=self.stage_id,
                ))

        return {"suggestions": len(names), "bound": added, "names": names}

    def should_bypass(self, state: PipelineState) -> bool:
        # 도구/RAG/capability/builtin 중 하나라도 있으면 실행.
        # ToolSource 가 등록됐으면 공급자 있음 → 실행.
        has_tools = bool(state.tool_definitions)
        has_rag = bool(self.get_param("rag_collections", state, []))
        has_sources = bool(get_tool_sources())
        has_caps = bool(state.config and getattr(state.config, "capabilities", None))
        has_builtins = bool(self.get_param("builtin_tools", state, []))
        return not (has_tools or has_rag or has_sources or has_caps or has_builtins)

    def list_strategies(self) -> list[StrategyInfo]:
        # v1.0 — capability_auto 카드 추가 (구 s05_strategy 흡수).
        # capability_auto 픽 → 자연어 intent 로 capability 후보 자동 발견·바인딩.
        return [
            StrategyInfo("progressive_3level", "3단계 점진적 디스커버리", is_default=True),
            StrategyInfo("eager_load", "모든 도구 스키마를 즉시 로드"),
            StrategyInfo("capability_auto", "자연어 intent → capability 자동 발견·바인딩"),
            StrategyInfo("none", "도구 인덱싱 비활성화"),
        ]
