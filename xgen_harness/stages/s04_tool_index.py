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

        # 1. 선택된 MCP 세션에서 도구 디스커버리
        #    stage_params에 mcp_sessions가 있으면 해당 세션만,
        #    없으면 s01_input에서 이미 수집한 tool_definitions 유지 (하위 호환)
        if selected_mcp_sessions:
            await self._discover_selected_mcp_tools(selected_mcp_sessions, state)

        # 2. Strategy 디스패치로 인덱스 생성 (progressive_3level / eager_load / none)
        strategy = self.resolve_strategy("discovery", state, "progressive_3level")
        if not strategy:
            from .strategies.discovery import ProgressiveDiscovery
            strategy = ProgressiveDiscovery()
        tool_index, augmented_defs = await strategy.discover(state.tool_definitions, state)

        state.tool_definitions = augmented_defs
        state.tool_index = tool_index

        # 3. RAG 설정을 metadata에 저장 (s03_system_prompt에서 사용)
        if rag_collections:
            state.metadata["rag_collections"] = rag_collections
            state.metadata["rag_top_k"] = rag_top_k
            logger.info("[Tool Index] RAG collections: %s (top_k=%d)", rag_collections, rag_top_k)

        logger.info("[Tool Index] %d tools indexed, %d definitions bound",
                     len(tool_index), len(state.tool_definitions))
        return {
            "tools_count": len(tool_index),
            "tools_bound": len(state.tool_definitions),
            "mcp_sessions_selected": len(selected_mcp_sessions),
            "rag_collections": len(rag_collections),
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
        # 도구가 없더라도 RAG 컬렉션이 있으면 실행 (metadata에 저장해야 하므로)
        has_tools = bool(state.tool_definitions)
        has_rag = bool(self.get_param("rag_collections", state, []))
        has_mcp = bool(self.get_param("mcp_sessions", state, []))
        return not (has_tools or has_rag or has_mcp)

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("progressive_3level", "3단계 점진적 디스커버리", is_default=True),
            StrategyInfo("eager_load", "모든 도구 스키마를 즉시 로드"),
            StrategyInfo("none", "도구 인덱싱 비활성화"),
        ]
