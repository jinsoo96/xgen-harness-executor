"""
Decide strategies — s08_decide 용 Strategy 구현체 (v1.0)

v0.17.0 책임 분리:
  - Guard 호출은 전부 s05_policy Stage (Policy Gate) 로 이관.
  - 본 모듈은 "현 state 로 루프를 계속/완료/재시도" 판단만 수행.
  - s05_policy 가 block 을 걸면 state.loop_decision 을 미리 설정하므로
    ThresholdDecide 는 그 신호를 존중하여 즉시 complete.

구현체:
  ThresholdDecide — 도구 호출/검증 점수/텍스트 응답 기반 판단 (기본)
  AlwaysPassDecide — 항상 complete (루프 없음, 1회 실행)
"""

import logging
from typing import Any

from ..interfaces import DecideStrategy

logger = logging.getLogger("harness.strategy.decide")

LOOP_CONTINUE = "continue"
LOOP_COMPLETE = "complete"
LOOP_RETRY = "retry"
LOOP_ERROR = "error"
LOOP_ESCALATE = "escalate"


# default 값은 외부 override 가능한 단일 레지스트리. v1.0.2 "정책 default 박제 정리"
# 룰을 strategy 영역에도 적용 — magic number 가 함수 본문에 박히지 않도록 한다.
_DECIDE_DEFAULTS: dict[str, Any] = {
    "max_retries": 3,
    "validation_threshold": 0.7,
}


def register_decide_defaults(**kwargs: Any) -> None:
    """외부 갤러리/이식측에서 decide 기본값을 조정.

    예: `register_decide_defaults(max_retries=5)` → ThresholdDecide.decide 가
    params 에 max_retries 가 없을 때 5 사용.
    """
    for k, v in kwargs.items():
        if k in _DECIDE_DEFAULTS:
            _DECIDE_DEFAULTS[k] = v


def get_decide_default(key: str) -> Any:
    return _DECIDE_DEFAULTS.get(key)


class ThresholdDecide(DecideStrategy):
    """도구 호출 대기 / 검증 점수 / 텍스트 응답 기반 루프 판단.

    v0.17.0 — Guard 체인 호출 제거. Policy Gate (s05_policy) 가 이미 loop_boundary
    훅에서 Guard 를 실행해 state.policy_block_reason / loop_decision 을 설정.
    본 Strategy 는 순수 루프 판단만 담당.
    """

    @property
    def name(self) -> str:
        return "threshold"

    @property
    def description(self) -> str:
        return "도구 호출 / 검증 점수 / 응답 기반 루프 판단 (Policy Gate 결과 존중)"

    async def decide(self, state: Any, params: dict[str, Any]) -> dict[str, Any]:
        # Policy Gate (s05_policy) 가 이미 block 을 걸어놨으면 그 결정을 존중.
        policy_reason = getattr(state, "policy_block_reason", None)
        if policy_reason:
            return {
                "decision": LOOP_COMPLETE,
                "reason": f"Policy Gate 차단: {policy_reason}",
                "guard": getattr(state, "policy_block_guard", "") or "",
            }

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
            default_threshold = _DECIDE_DEFAULTS["validation_threshold"]
            threshold = getattr(config, "validation_threshold", default_threshold) if config else default_threshold
            if validation_score < threshold:
                retry_count = int(getattr(state, "retry_count", 0) or 0)
                default_max = _DECIDE_DEFAULTS["max_retries"]
                max_retries = int(params.get("max_retries", default_max) or default_max)
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
