"""
S05 Plan — 선택적 CoT 계획 단계

기본적으로 bypass. full 프리셋에서만 활성화.
활성화되면 LLM에게 계획을 먼저 수립하도록 요청.
"""

import logging

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState

logger = logging.getLogger("harness.stage.plan")


class PlanStage(Stage):

    @property
    def stage_id(self) -> str:
        return "s05_plan"

    @property
    def order(self) -> int:
        return 5

    def should_bypass(self, state: PipelineState) -> bool:
        # 2번째 루프부터는 bypass (재계획 불필요)
        return state.loop_iteration > 1

    async def execute(self, state: PipelineState) -> dict:
        # 계획 지시를 시스템 프롬프트에 추가
        planning_instruction = (
            "\n\n<planning_instruction>\n"
            "Before answering, think step by step about what information you need "
            "and which tools to use. Create a brief plan, then execute it.\n"
            "</planning_instruction>"
        )

        if planning_instruction not in state.system_prompt:
            state.system_prompt += planning_instruction

        logger.info("[Plan] Planning instruction added to system prompt")
        return {"planning_enabled": True}

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("cot_planner", "Chain-of-Thought 계획 수립", is_default=True),
            StrategyInfo("none", "계획 단계 비활성화"),
        ]
