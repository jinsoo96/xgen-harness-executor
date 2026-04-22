"""
S09 Decide — 루프 계속/완료 판단 (v0.14.0 번호 시프트: s10_decide → s09_decide)

Strategy 에 전적 위임 — Stage 내부에 분기 로직 없음.
각 DecideStrategy 구현체가 자기 판단 규칙을 전부 들고 있다.

기본 Strategy:
  threshold (ThresholdDecide): Guard 체인 + 도구 호출 + 점수 + 응답 기반
  always_pass (AlwaysPassDecide): 항상 complete

v0.11.1 리팩토링: 기존 execute() 안 if/else 하드코딩을 ThresholdDecide.decide() 로 이관.
"""

import logging

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState
from ..strategies._decide import (
    LOOP_CONTINUE,
    LOOP_COMPLETE,
    LOOP_RETRY,
    LOOP_ERROR,
    LOOP_ESCALATE,
)

logger = logging.getLogger("harness.stage.decide")

# geny 패턴: loop decision 상수 재노출 (하위 호환 — 외부 import 하는 코드 보호)
__all__ = [
    "DecideStage",
    "LOOP_CONTINUE",
    "LOOP_COMPLETE",
    "LOOP_RETRY",
    "LOOP_ERROR",
    "LOOP_ESCALATE",
]


class DecideStage(Stage):
    """루프 계속/완료 판단 — 전적으로 DecideStrategy 에 위임."""

    @property
    def stage_id(self) -> str:
        return "s09_decide"

    @property
    def order(self) -> int:
        return 9

    async def execute(self, state: PipelineState) -> dict:
        strategy = self.resolve_strategy("decide", state, "threshold")
        if strategy is None:
            state.loop_decision = LOOP_ERROR
            reason = "Decide strategy 미등록 — threshold 를 찾지 못함"
            logger.error("[Decide] %s", reason)
            return {"decision": LOOP_ERROR, "reason": reason}

        params = {
            "guards": self.get_param("guards", state, None),
            "cost_budget_usd": self.get_param("cost_budget_usd", state, 0.0),
            "token_budget": self.get_param("token_budget", state, 0),
            "max_retries": self.get_param("max_retries", state, 3),
            # ContentGuard 설정 — 패턴/PII/대상
            "content_blocked_patterns": self.get_param("content_blocked_patterns", state, None),
            "content_detect_pii": self.get_param("content_detect_pii", state, False),
            "content_check_target": self.get_param("content_check_target", state, "both"),
        }

        try:
            result = await strategy.decide(state, params)
        except Exception as e:
            logger.exception("[Decide] Strategy '%s' 실행 실패", strategy.name)
            state.loop_decision = LOOP_ERROR
            return {"decision": LOOP_ERROR, "reason": f"{strategy.name} 실패: {e}"}

        decision = result.get("decision", LOOP_CONTINUE)
        state.loop_decision = decision
        return result

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("threshold", "Guard 체인 + 점수 기반 판단", is_default=True),
            StrategyInfo("always_pass", "항상 완료 (루프 없음)"),
        ]
