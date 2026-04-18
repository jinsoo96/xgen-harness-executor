"""
S04 Tool Index — Progressive 도구 디스커버리

Level 1: 도구 메타데이터(이름+설명)를 시스템 프롬프트에 삽입
Level 2: discover_tools 빌트인 도구로 상세 스키마 조회
Level 3: 실제 도구 실행 (s08_execute에서 처리)

UI에서 선택한 도구/MCP/RAG를 여기서 바인딩.
stage_params에서 mcp_sessions, rag_collections, rag_top_k를 읽어
선택된 세션만 디스커버리하고, RAG 설정을 metadata에 저장한다.
"""

import logging

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..tools.builtin import DiscoverToolsTool
from ..tools.rag_tool import RAGSearchTool

logger = logging.getLogger("harness.stage.tool_index")


class ToolIndexStage(Stage):
    """도구 색인 + progressive disclosure 설정"""

    @property
    def stage_id(self) -> str:
        return "s04_tool_index"

    @property
    def order(self) -> int:
        return 4

    async def execute(self, state: PipelineState) -> dict:
        # 0. stage_params에서 설정 읽기 (3-level fallback)
        selected_mcp_sessions: list[str] = self.get_param("mcp_sessions", state, [])
        rag_collections: list[str] = self.get_param("rag_collections", state, [])
        rag_top_k: int = self.get_param("rag_top_k", state, 4)

        # 0.5. Capability 바인딩 — 선언된 capability를 Tool 인스턴스로 materialize
        cap_result = self._bind_capabilities(state)
        # verbose: 선언된 capability 각각 발행
        if cap_result.get("_events"):
            from ..events.types import CapabilityBindEvent
            for ev in cap_result["_events"]:
                await state.emit_verbose(CapabilityBindEvent(
                    name=ev["name"], source=ev["source"], stage_id=self.stage_id,
                ))

        # 1. 선택된 MCP 세션에서 도구 디스커버리
        #    stage_params에 mcp_sessions가 있으면 해당 세션만,
        #    없으면 s01_input에서 이미 수집한 tool_definitions 유지 (하위 호환)
        if selected_mcp_sessions:
            await self._discover_selected_mcp_tools(selected_mcp_sessions, state)

        # 2. builtin_tools 필터링 — 선택된 빌트인만 추가
        selected_builtins: list[str] = self.get_param("builtin_tools", state, ["discover_tools"])

        # 3. Strategy 디스패치로 인덱스 생성 (progressive_3level / eager_load / none)
        strategy = self.resolve_strategy("discovery", state, "progressive_3level")
        if not strategy:
            from .strategies.discovery import ProgressiveDiscovery
            strategy = ProgressiveDiscovery()
        tool_index, augmented_defs = await strategy.discover(state.tool_definitions, state)

        # discover_tools 빌트인은 selected_builtins에 포함된 경우만 유지
        if "discover_tools" not in selected_builtins:
            augmented_defs = [td for td in augmented_defs if td.get("name") != "discover_tools"]
            tool_index = {k: v for k, v in tool_index.items() if k != "discover_tools"}
            logger.info("[Tool Index] discover_tools excluded (not in builtin_tools)")

        state.tool_definitions = augmented_defs
        state.tool_index = tool_index

        # 3. RAG 설정을 metadata에 저장 (s03_system_prompt에서 사용)
        if rag_collections:
            state.metadata["rag_collections"] = rag_collections
            state.metadata["rag_top_k"] = rag_top_k
            logger.info("[Tool Index] RAG collections: %s (top_k=%d)", rag_collections, rag_top_k)

            # RAG tool mode: 에이전트가 직접 호출할 수 있는 rag_search 도구 등록
            rag_tool_mode: str = self.get_param("rag_tool_mode", state, "both")
            if rag_tool_mode in ("tool", "both"):
                rag_tool = RAGSearchTool(
                    collections=rag_collections,
                    default_top_k=rag_top_k,
                )
                # 중복 방지
                if not any(td.get("name") == "rag_search" for td in state.tool_definitions):
                    state.tool_definitions.append(rag_tool.to_api_format())
                    tool_index.append(rag_tool.to_index_entry())
                    # tool_registry에 인스턴스 등록 (s08_execute에서 실행용)
                    if "tool_registry" not in state.metadata:
                        state.metadata["tool_registry"] = {}
                    state.metadata["tool_registry"]["rag_search"] = rag_tool
                    logger.info("[Tool Index] rag_search tool registered (mode=%s)", rag_tool_mode)

        logger.info("[Tool Index] %d tools indexed, %d definitions bound",
                     len(tool_index), len(state.tool_definitions))
        return {
            "tools_count": len(tool_index),
            "tools_bound": len(state.tool_definitions),
            "mcp_sessions_selected": len(selected_mcp_sessions),
            "rag_collections": len(rag_collections),
            "capabilities_declared": cap_result.get("declared", 0),
            "capabilities_resolved": cap_result.get("resolved", 0),
            "capabilities_unknown": cap_result.get("unknown", 0),
        }

    def _bind_capabilities(self, state: PipelineState) -> dict:
        """config.capabilities를 Tool 인스턴스로 materialize 후 state에 반영.

        선언 없으면 no-op. 누락된 factory/unknown capability는 경고만 찍고 계속.
        """
        config = state.config
        if config is None or not getattr(config, "capabilities", None):
            return {"declared": 0, "resolved": 0, "unknown": 0}

        from ..capabilities import materialize_capabilities, merge_into_state

        declared_names = list(config.capabilities)
        report = materialize_capabilities(
            declared_names,
            capability_params=getattr(config, "capability_params", None),
        )
        added = merge_into_state(report, state)

        logger.info(
            "[Tool Index] capabilities: declared=%d, resolved=%d, added=%d, unknown=%d, no_factory=%d",
            len(declared_names),
            len(report.resolved),
            added,
            len(report.unknown),
            len(report.no_factory),
        )

        if report.unknown:
            logger.warning("[Tool Index] unknown capabilities (missing in registry): %s", report.unknown)
        if report.no_factory:
            logger.warning(
                "[Tool Index] capabilities without tool_factory (Adapter 주입 필요): %s",
                report.no_factory,
            )

        # verbose: 선언 바인딩된 capability 각각 이벤트 발행 (비동기로는 못 해서 동기 큐 푸시)
        # emit_verbose 는 async 이므로 여기서는 이벤트 준비만 하고 _bind_capabilities 는 sync.
        # → async 로 바꾸거나, state.metadata 에 기록 후 다른 지점에서 flush.
        # 가장 단순: async 호출 가능하도록 이 메서드 호출부를 await 으로 바꿈. 아래서 처리.

        return {
            "declared": len(declared_names),
            "resolved": len(report.resolved),
            "unknown": len(report.unknown),
            "_events": [
                {"name": n, "source": "declaration"} for n in report.resolved
            ],
        }

    async def _discover_selected_mcp_tools(
        self, session_ids: list[str], state: PipelineState
    ) -> None:
        """선택된 MCP 세션에서만 도구를 디스커버리하여 state에 등록"""
        try:
            from ..tools.mcp_client import discover_mcp_tools
            mcp_tools = await discover_mcp_tools(session_ids)

            tool_mapping = state.metadata.get("mcp_tool_mapping", {})
            if "tool_registry" not in state.metadata:
                state.metadata["tool_registry"] = {}

            for tool in mcp_tools:
                # 이미 등록된 도구는 건너뛰기 (s01_input에서 등록한 것과 중복 방지)
                if any(td.get("name") == tool.name for td in state.tool_definitions):
                    continue
                state.tool_definitions.append(tool.to_api_format())
                tool_mapping[tool.name] = tool._session_id
                state.metadata["tool_registry"][tool.name] = tool

            state.metadata["mcp_tool_mapping"] = tool_mapping
            logger.info("[Tool Index] MCP discovery: %d tools from %d selected sessions",
                        len(mcp_tools), len(session_ids))
        except Exception as e:
            logger.warning("[Tool Index] MCP discovery failed: %s", e)

    def should_bypass(self, state: PipelineState) -> bool:
        # 도구/RAG/MCP/capability/builtin 중 하나라도 있으면 실행
        has_tools = bool(state.tool_definitions)
        has_rag = bool(self.get_param("rag_collections", state, []))
        has_mcp = bool(self.get_param("mcp_sessions", state, []))
        has_caps = bool(state.config and getattr(state.config, "capabilities", None))
        has_builtins = bool(self.get_param("builtin_tools", state, []))
        return not (has_tools or has_rag or has_mcp or has_caps or has_builtins)

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("progressive_3level", "3단계 점진적 디스커버리", is_default=True),
            StrategyInfo("eager_load", "모든 도구 스키마를 즉시 로드"),
            StrategyInfo("none", "도구 인덱싱 비활성화"),
        ]
