"""
S10 Decide — 루프 계속/완료 판단

geny-harness s13_loop 차용:
  LoopController: continue / complete / error / escalate
  Guard 체인으로 가드레일 체크 (하드코딩 제거)

판단 흐름:
1. Guard 체인 실행 (비용/반복/토큰 예산)
   → 차단되면 complete (강제 종료)
2. 도구 호출 대기 → continue
3. 검증 점수 미달 → retry
4. 텍스트 응답 있음 → complete
5. 기본 → continue
"""

import logging

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState

logger = logging.getLogger("harness.stage.decide")

# geny 패턴: loop decision 상수
LOOP_CONTINUE = "continue"
LOOP_COMPLETE = "complete"
LOOP_RETRY = "retry"
LOOP_ERROR = "error"
LOOP_ESCALATE = "escalate"  # geny 차용 — 상위 오케스트레이터에 에스컬레이션


class DecideStage(Stage):
    """루프 계속/완료 판단 — Guard 체인 + Loop Controller"""

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
            state.loop_decision = LOOP_COMPLETE
            return {"decision": LOOP_COMPLETE, "reason": "always_pass strategy"}

        # ── 1. Guard 체인 실행 (하드코딩 제거) ──
        from .strategies.guard import create_default_guard_chain
        guard_chain = create_default_guard_chain()
        guard_results = guard_chain.check_all(state)

        blocked = [r for r in guard_results if not r.passed and r.severity == "block"]
        warnings = [r for r in guard_results if r.passed and r.severity == "warn"]

        if blocked:
            state.loop_decision = LOOP_COMPLETE
            reason = f"Guard 차단: {blocked[0].guard_name} — {blocked[0].reason}"
            logger.warning("[Decide] %s", reason)
            return {"decision": LOOP_COMPLETE, "reason": reason, "guard": blocked[0].guard_name}

        for w in warnings:
            logger.info("[Decide] Guard 경고: %s — %s", w.guard_name, w.reason)

        # ── 2. 도구 호출 대기 → continue ──
        if state.pending_tool_calls:
            state.loop_decision = LOOP_CONTINUE
            reason = f"도구 호출 {len(state.pending_tool_calls)}건 대기"
            return {"decision": LOOP_CONTINUE, "reason": reason}

        # ── 3. 검증 점수 미달 → retry ──
        config = state.config
        if state.validation_score is not None:
            threshold = config.validation_threshold if config else 0.7
            if state.validation_score < threshold:
                if state.retry_count < (config.max_retries if config else 3):
                    state.loop_decision = LOOP_RETRY
                    reason = f"검증 점수 미달 ({state.validation_score:.2f} < {threshold})"
                    logger.info("[Decide] %s → retry", reason)
                    return {"decision": LOOP_RETRY, "reason": reason}
                else:
                    state.loop_decision = LOOP_COMPLETE
                    reason = f"재시도 한도 도달 (점수 {state.validation_score:.2f})"
                    return {"decision": LOOP_COMPLETE, "reason": reason}

        # ── 4. 텍스트 응답 있음 → complete ──
        if state.last_assistant_text:
            state.loop_decision = LOOP_COMPLETE
            return {"decision": LOOP_COMPLETE, "reason": "응답 생성 완료"}

        # ── 5. 기본 → continue ──
        state.loop_decision = LOOP_CONTINUE
        return {"decision": LOOP_CONTINUE, "reason": "추가 처리 필요"}

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("threshold", "Guard 체인 + 점수 기반 판단", is_default=True),
            StrategyInfo("always_pass", "항상 완료 (루프 없음)"),
        ]
