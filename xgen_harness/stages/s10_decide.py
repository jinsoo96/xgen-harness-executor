"""
S10 Decide — 루프 계속/완료 판단

판단 기준:
1. 도구 호출이 있었으면 → continue (LLM이 도구 결과를 처리해야 함)
2. 도구 호출 없고 텍스트가 있으면 → complete
3. 검증 점수가 threshold 미만이면 → retry
4. 반복 횟수/비용 초과 → complete (강제 종료)
"""

import logging

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState

logger = logging.getLogger("harness.stage.decide")


class DecideStage(Stage):
    """루프 계속/완료 판단"""

    @property
    def stage_id(self) -> str:
        return "s10_decide"

    @property
    def order(self) -> int:
        return 10

    async def execute(self, state: PipelineState) -> dict:
        # ── Strategy 디스패치 ──
        strategy = self.resolve_strategy("decide", state, "threshold")
        if strategy and strategy.name == "always_pass":
            state.loop_decision = "complete"
            return {"decision": "complete", "reason": "always_pass strategy"}

        config = state.config
        reason = ""

        # 1. 비용 초과 → 강제 완료
        if state.is_over_budget:
            state.loop_decision = "complete"
            reason = f"비용 예산 초과 (${state.cost_usd:.4f})"
            logger.warning("[Decide] %s", reason)
            return {"decision": "complete", "reason": reason}

        # 2. 반복 초과 → 강제 완료
        if state.is_over_iterations:
            state.loop_decision = "complete"
            reason = f"최대 반복 횟수 도달 ({state.loop_iteration})"
            logger.warning("[Decide] %s", reason)
            return {"decision": "complete", "reason": reason}

        # 3. 도구 호출이 있었으면 → continue (Execute 스테이지 결과를 LLM이 처리)
        if state.pending_tool_calls:
            state.loop_decision = "continue"
            reason = f"도구 호출 {len(state.pending_tool_calls)}건 대기"
            return {"decision": "continue", "reason": reason}

        # 4. 검증 점수 확인 (Validate 스테이지가 활성화된 경우)
        if state.validation_score is not None:
            threshold = config.validation_threshold if config else 0.7
            if state.validation_score < threshold:
                if state.retry_count < (config.max_retries if config else 3):
                    state.loop_decision = "retry"
                    reason = f"검증 점수 미달 ({state.validation_score:.2f} < {threshold})"
                    logger.info("[Decide] %s → retry", reason)
                    return {"decision": "retry", "reason": reason, "retry": True}
                else:
                    state.loop_decision = "complete"
                    reason = f"재시도 한도 도달 (점수 {state.validation_score:.2f})"
                    return {"decision": "complete", "reason": reason}

        # 5. 텍스트 응답이 있으면 → complete
        if state.last_assistant_text:
            state.loop_decision = "complete"
            reason = "응답 생성 완료"
            return {"decision": "complete", "reason": reason}

        # 6. 기본 → continue
        state.loop_decision = "continue"
        reason = "추가 처리 필요"
        return {"decision": "continue", "reason": reason}

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("threshold", "점수 기반 + 도구 상태 판단", is_default=True),
            StrategyInfo("always_pass", "항상 완료 (루프 없음)"),
        ]
