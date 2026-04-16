"""
S02 Memory — 대화 이력/기억 로드

- 이전 실행 결과를 state.previous_results에서 가져옴
- conversation_history가 있으면 messages에 추가
- 기억이 없으면 bypass
"""

import logging

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState

logger = logging.getLogger("harness.stage.memory")


class MemoryStage(Stage):

    @property
    def stage_id(self) -> str:
        return "s02_memory"

    @property
    def order(self) -> int:
        return 2

    def should_bypass(self, state: PipelineState) -> bool:
        return not state.previous_results and not state.conversation_history

    async def execute(self, state: PipelineState) -> dict:
        injected = 0
        max_history = int(self.get_param("max_history", state, 10))

        # 1. 대화 이력이 있으면 messages에 추가 (max_history로 제한)
        if state.conversation_history:
            history = state.conversation_history[-max_history:]
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    state.messages.insert(len(state.messages) - 1, {"role": role, "content": content})
                    injected += 1

        # 2. 이전 실행 결과를 previous_results로 system prompt에 전달
        # (s03_system_prompt에서 처리 — 여기서는 state에 이미 있으므로 패스)
        prev_count = len(state.previous_results)

        logger.info("[Memory] injected=%d messages (max_history=%d), previous_results=%d", injected, max_history, prev_count)
        return {
            "injected": injected,
            "previous_results": prev_count,
        }

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "이전 실행 결과 + 대화 이력 로드", is_default=True),
            StrategyInfo("embedding_search", "임베딩 기반 관련 기억 검색"),
        ]
