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
        mode = self.get_param("planning_mode", state, "auto")

        # "auto" 모드: input_complexity에 따라 planning depth 결정
        if mode == "auto":
            complexity = state.metadata.get("input_complexity", "moderate")
            if complexity == "simple":
                mode = "none"
            elif complexity == "complex":
                mode = "react"
            else:
                mode = "cot"
            logger.info("[Plan] auto mode resolved to '%s' (complexity=%s)", mode, complexity)

        # mode == "none": 계획 단계 비활성화
        if mode == "none":
            logger.info("[Plan] planning_mode=none, bypassed")
            return {"planning_enabled": False, "planning_mode": "none"}

        # mode == "react": ReAct-style prompt (복잡한 멀티스텝 태스크)
        if mode == "react":
            planning_instruction = (
                "\n\n<planning_instruction>\n"
                "Use the ReAct (Reason + Act) framework:\n"
                "1. Thought: Analyze the current situation and decide the next action.\n"
                "2. Action: Execute a tool or generate a response.\n"
                "3. Observation: Review the result and decide if more steps are needed.\n"
                "Repeat until the task is complete.\n"
                "</planning_instruction>"
            )
        else:
            # 기본 CoT (moderate 복잡도)
            planning_instruction = (
                "\n\n<planning_instruction>\n"
                "Before answering, think step by step about what information you need "
                "and which tools to use. Create a brief plan, then execute it.\n"
                "</planning_instruction>"
            )

        if planning_instruction not in state.system_prompt:
            state.system_prompt += planning_instruction

        logger.info("[Plan] Planning instruction added (mode=%s)", mode)
        return {"planning_enabled": True, "planning_mode": mode}

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("cot_planner", "Chain-of-Thought 계획 수립", is_default=True),
            StrategyInfo("none", "계획 단계 비활성화"),
        ]
