"""
Guard strategies — 가드레일 체인

geny-harness s04_guard 차용:
  Guard trait: 단일 가드 체크
  GuardChain trait: 체인으로 묶어서 실행

가드레일 종류:
- TokenBudgetGuard: 토큰 예산 초과 체크
- CostBudgetGuard: 비용 예산 초과 체크
- IterationGuard: 반복 횟수 초과 체크
- ContentGuard: 입력/출력 콘텐츠 필터링 (확장용)
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..interfaces import Strategy

logger = logging.getLogger("harness.strategy.guard")


@dataclass
class GuardResult:
    """가드 체크 결과"""
    passed: bool
    guard_name: str
    reason: str = ""
    severity: str = "block"  # "block" | "warn" | "info"


class Guard(Strategy, ABC):
    """단일 가드 체크 인터페이스"""

    @abstractmethod
    def check(self, state: Any) -> GuardResult:
        """state를 검사하여 통과/차단 결정"""
        ...


class GuardChain(Strategy):
    """가드 체인 — 여러 가드를 순서대로 실행"""

    def __init__(self):
        self._guards: list[Guard] = []

    @property
    def name(self) -> str:
        return "guard_chain"

    def add(self, guard: Guard) -> "GuardChain":
        self._guards.append(guard)
        return self

    def check_all(self, state: Any, short_circuit: bool = True) -> list[GuardResult]:
        """모든 가드 실행. short_circuit=True면 첫 차단에서 중단."""
        results = []
        for guard in self._guards:
            result = guard.check(state)
            results.append(result)
            if not result.passed and short_circuit and result.severity == "block":
                logger.warning("[Guard] Blocked by %s: %s", result.guard_name, result.reason)
                break
        return results

    def is_passed(self, state: Any) -> bool:
        """전체 통과 여부"""
        results = self.check_all(state)
        return all(r.passed for r in results)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  기본 Guard 구현체
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TokenBudgetGuard(Guard):
    """토큰 예산 초과 체크"""

    def __init__(self, token_budget: int = 0):
        self._token_budget = token_budget

    @property
    def name(self) -> str:
        return "token_budget"

    def check(self, state: Any) -> GuardResult:
        if not hasattr(state, 'token_usage'):
            return GuardResult(passed=True, guard_name=self.name)

        # 우선순위: 생성자 인자 > config.context_window > 기본값
        if self._token_budget > 0:
            max_tokens = self._token_budget
        elif hasattr(state, 'config') and state.config:
            max_tokens = getattr(state.config, 'context_window', 1_000_000)
        else:
            max_tokens = 1_000_000

        used = state.token_usage.total if hasattr(state.token_usage, 'total') else 0

        if used > max_tokens * 0.95:
            return GuardResult(
                passed=False,
                guard_name=self.name,
                reason=f"토큰 예산 95% 초과 ({used}/{max_tokens})",
                severity="block",
            )
        if used > max_tokens * 0.8:
            return GuardResult(
                passed=True,
                guard_name=self.name,
                reason=f"토큰 예산 80% 경고 ({used}/{max_tokens})",
                severity="warn",
            )
        return GuardResult(passed=True, guard_name=self.name)


class CostBudgetGuard(Guard):
    """비용 예산 초과 체크"""

    def __init__(self, cost_budget_usd: float = 0.0):
        self._cost_budget_usd = cost_budget_usd

    @property
    def name(self) -> str:
        return "cost_budget"

    def check(self, state: Any) -> GuardResult:
        if not hasattr(state, 'cost_usd'):
            return GuardResult(passed=True, guard_name=self.name)

        # 우선순위: 생성자 인자 > config.cost_budget_usd > 기본값
        if self._cost_budget_usd > 0:
            budget = self._cost_budget_usd
        elif hasattr(state, 'config') and state.config:
            budget = getattr(state.config, 'cost_budget_usd', 10.0)
        else:
            budget = 10.0

        cost = state.cost_usd

        if cost >= budget:
            return GuardResult(
                passed=False,
                guard_name=self.name,
                reason=f"비용 예산 초과 (${cost:.4f} >= ${budget:.2f})",
                severity="block",
            )
        return GuardResult(passed=True, guard_name=self.name)


class IterationGuard(Guard):
    """반복 횟수 초과 체크"""

    @property
    def name(self) -> str:
        return "iteration"

    def check(self, state: Any) -> GuardResult:
        if not hasattr(state, 'loop_iteration') or not hasattr(state, 'config'):
            return GuardResult(passed=True, guard_name=self.name)

        max_iter = getattr(state.config, 'max_iterations', 10)
        current = state.loop_iteration

        if current >= max_iter:
            return GuardResult(
                passed=False,
                guard_name=self.name,
                reason=f"최대 반복 횟수 도달 ({current}/{max_iter})",
                severity="block",
            )
        return GuardResult(passed=True, guard_name=self.name)


class ContentGuard(Guard):
    """콘텐츠 필터링 (확장용)"""

    @property
    def name(self) -> str:
        return "content"

    def check(self, state: Any) -> GuardResult:
        # 확장 포인트 — 입력/출력 콘텐츠 검사
        # 예: PII 감지, 금지어 필터, 토픽 제한 등
        return GuardResult(passed=True, guard_name=self.name)


# 사용 가능한 가드 이름 → 클래스 매핑
ALL_GUARD_NAMES: list[str] = ["iteration", "cost_budget", "token_budget", "content"]

_GUARD_REGISTRY: dict[str, type[Guard]] = {
    "iteration": IterationGuard,
    "cost_budget": CostBudgetGuard,
    "token_budget": TokenBudgetGuard,
    "content": ContentGuard,
}


def create_guard_chain(
    guards: list[str] | None = None,
    cost_budget_usd: float = 0.0,
    token_budget: int = 0,
) -> GuardChain:
    """설정 가능한 가드 체인 생성.

    Args:
        guards: 활성화할 가드 이름 목록. None이면 모든 가드 활성화.
                사용 가능: "iteration", "cost_budget", "token_budget", "content"
        cost_budget_usd: CostBudgetGuard 임계값 (0이면 config/기본값 사용)
        token_budget: TokenBudgetGuard 임계값 (0이면 config/기본값 사용)
    """
    enabled = guards if guards is not None else ALL_GUARD_NAMES

    chain = GuardChain()
    for name in enabled:
        if name not in _GUARD_REGISTRY:
            logger.warning("[Guard] Unknown guard name '%s', skipping", name)
            continue

        if name == "cost_budget":
            chain.add(CostBudgetGuard(cost_budget_usd=cost_budget_usd))
        elif name == "token_budget":
            chain.add(TokenBudgetGuard(token_budget=token_budget))
        else:
            chain.add(_GUARD_REGISTRY[name]())

    return chain


def create_default_guard_chain() -> GuardChain:
    """기본 가드 체인 생성 (하위 호환)"""
    return create_guard_chain()
