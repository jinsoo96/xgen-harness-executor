"""
Decide strategies — s10_decide 용 Strategy 구현체

Stage 는 이 Strategy 에 판단을 전적으로 위임한다 — Stage 내부에 분기 로직 없음.

구현체:
  ThresholdDecide: Guard 체인 + 도구 호출/점수/응답 기반 판단 (기본)
  AlwaysPassDecide: 항상 complete (루프 없음, 1회 실행)
"""

import logging
from typing import Any

from ..interfaces import DecideStrategy

logger = logging.getLogger("harness.strategy.decide")

# 루프 판단 상수 — s10_decide 가 이 모듈에서 import
LOOP_CONTINUE = "continue"
LOOP_COMPLETE = "complete"
LOOP_RETRY = "retry"
LOOP_ERROR = "error"
LOOP_ESCALATE = "escalate"


class ThresholdDecide(DecideStrategy):
    """Guard 체인 + 도구/점수/응답 기반 루프 판단."""

    @property
    def name(self) -> str:
        return "threshold"

    @property
    def description(self) -> str:
        return "Guard 체인 + 도구 호출 + 검증 점수 기반 판단"

    async def decide(self, state: Any, params: dict[str, Any]) -> dict[str, Any]:
        from .guard import create_guard_chain

        guard_chain = create_guard_chain(
            guards=params.get("guards"),
            cost_budget_usd=float(params.get("cost_budget_usd", 0.0) or 0.0),
            token_budget=int(params.get("token_budget", 0) or 0),
            content_blocked_patterns=params.get("content_blocked_patterns"),
            content_detect_pii=bool(params.get("content_detect_pii", False)),
            content_check_target=str(params.get("content_check_target", "both")),
        )
        guard_results = guard_chain.check_all(state)
        blocked = [r for r in guard_results if not r.passed and r.severity == "block"]
        warnings = [r for r in guard_results if r.passed and r.severity == "warn"]

        if blocked:
            reason = f"Guard 차단: {blocked[0].guard_name} — {blocked[0].reason}"
            logger.warning("[ThresholdDecide] %s", reason)
            return {
                "decision": LOOP_COMPLETE,
                "reason": reason,
                "guard": blocked[0].guard_name,
            }

        for w in warnings:
            logger.info("[ThresholdDecide] Guard 경고: %s — %s", w.guard_name, w.reason)

        # 도구 호출 대기 → continue
        pending = getattr(state, "pending_tool_calls", None) or []
        if pending:
            return {
                "decision": LOOP_CONTINUE,
                "reason": f"도구 호출 {len(pending)}건 대기",
            }

        # 검증 점수 기반 retry
        validation_score = getattr(state, "validation_score", None)
        if validation_score is not None:
            config = getattr(state, "config", None)
            threshold = getattr(config, "validation_threshold", 0.7) if config else 0.7
            if validation_score < threshold:
                retry_count = int(getattr(state, "retry_count", 0) or 0)
                max_retries = int(params.get("max_retries", 3) or 3)
                if retry_count < max_retries:
                    return {
                        "decision": LOOP_RETRY,
                        "reason": f"검증 점수 미달 ({validation_score:.2f} < {threshold})",
                    }
                return {
                    "decision": LOOP_COMPLETE,
                    "reason": f"재시도 한도 도달 ({retry_count}/{max_retries}, 점수 {validation_score:.2f})",
                }

        # 텍스트 응답 있음 → complete
        if getattr(state, "last_assistant_text", ""):
            return {"decision": LOOP_COMPLETE, "reason": "응답 생성 완료"}

        # 기본 → continue
        return {"decision": LOOP_CONTINUE, "reason": "추가 처리 필요"}


class AlwaysPassDecide(DecideStrategy):
    """1회 실행 후 즉시 complete — 단발성 워크플로우용."""

    @property
    def name(self) -> str:
        return "always_pass"

    @property
    def description(self) -> str:
        return "항상 완료 (루프 없음, 1회 실행)"

    async def decide(self, state: Any, params: dict[str, Any]) -> dict[str, Any]:
        return {"decision": LOOP_COMPLETE, "reason": "always_pass strategy"}
