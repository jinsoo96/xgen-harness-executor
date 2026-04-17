"""
S05 Plan — 계획 수립 단계

지원 모드:
- auto: complexity에 따라 none/cot/react 결정
- none: 계획 단계 비활성
- cot: Chain-of-Thought 지시 주입
- react: ReAct 지시 주입
- capability: 자연어 intent → CapabilityMatcher로 capability 자동 발견 + 동적 바인딩
"""

import logging

from ..capabilities import (
    CapabilityMatcher,
    MatchStrategy,
    get_default_registry,
    materialize_capabilities,
    merge_into_state,
)
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

        # capability 모드는 먼저 시도 (성공 여부 무관하게 CoT와 병행 가능)
        cap_result = None
        if mode == "capability" or self.get_param("capability_discovery", state, False):
            cap_result = await self._discover_and_bind_capabilities(state)

        # mode == "none" or mode == "capability" (capability 전용 모드): 계획 지시 skip
        if mode == "none":
            logger.info("[Plan] planning_mode=none, bypassed")
            return {"planning_enabled": False, "planning_mode": "none",
                    **(cap_result or {})}

        if mode == "capability":
            logger.info("[Plan] capability-only mode")
            return {
                "planning_enabled": True,
                "planning_mode": "capability",
                **(cap_result or {}),
            }

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
        return {
            "planning_enabled": True,
            "planning_mode": mode,
            **(cap_result or {}),
        }

    # ---------- Capability 모드 ----------

    async def _discover_and_bind_capabilities(self, state: PipelineState) -> dict:
        """
        자연어 intent(user_input)에서 capability 후보를 찾아 state에 바인딩.

        - 이미 config.capabilities에 선언된 것은 s04에서 처리됨 → 중복 회피
        - 여기서는 매칭된 것 중 아직 안 된 것만 materialize
        """
        if state.config is None:
            return {"capability_suggestions": 0, "capability_bound": 0}

        intent = state.user_input or ""
        if not intent.strip():
            return {"capability_suggestions": 0, "capability_bound": 0}

        already_bound = set(state.metadata.get("capability_bindings", {}).keys())
        already_declared = set(getattr(state.config, "capabilities", []) or [])
        skip = already_bound | already_declared

        top_k = int(self.get_param("capability_top_k", state, 3))
        min_score = float(self.get_param("capability_min_score", state, 0.4))

        registry = get_default_registry()
        matcher = CapabilityMatcher(registry, min_score=min_score)
        matches = matcher.match(intent, limit=top_k * 2, strategy=MatchStrategy.AUTO)

        suggested = [m for m in matches if m.spec.name not in skip][:top_k]
        if not suggested:
            logger.info("[Plan] capability discovery: no new matches (intent=%r)", intent[:80])
            return {"capability_suggestions": 0, "capability_bound": 0}

        names = [m.spec.name for m in suggested]
        state.metadata.setdefault("suggested_capabilities", []).extend(
            [{"name": m.spec.name, "score": m.score, "strategy": m.strategy} for m in suggested]
        )

        # factory가 있는 것만 materialize 시도
        report = materialize_capabilities(
            names,
            registry=registry,
            capability_params=getattr(state.config, "capability_params", None),
        )
        added = merge_into_state(report, state)

        logger.info(
            "[Plan] capability discovery: suggestions=%s, bound=%d, unknown=%d, no_factory=%d",
            names,
            added,
            len(report.unknown),
            len(report.no_factory),
        )

        return {
            "capability_suggestions": len(names),
            "capability_bound": added,
            "capability_names": names,
        }

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("cot_planner", "Chain-of-Thought 계획 수립", is_default=True),
            StrategyInfo("react", "ReAct 프레임워크 지시"),
            StrategyInfo("capability", "자연어 intent → capability 자동 발견"),
            StrategyInfo("none", "계획 단계 비활성화"),
        ]
