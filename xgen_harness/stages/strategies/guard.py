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
import re
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
    """콘텐츠 필터 — 금지 패턴 매칭 + 선택적 PII 감지.

    기본값은 패턴 없음 + PII 감지 off → 항상 통과 (하위 호환).
    configure() 로 활성화하거나 생성자에 패턴을 넘겨서 사용.

    params:
      blocked_patterns: 정규식 문자열 리스트 (대소문자 무시)
      detect_pii: True 면 _PII_PATTERNS 로 이메일/휴대폰/주민번호/카드번호 감지
      check_target: 'input' | 'output' | 'both' (기본 'both')
                     — input 은 마지막 user 메시지, output 은 last_assistant_text
    """

    # 한국 맥락 포함 기본 PII 패턴
    _PII_PATTERNS: dict[str, re.Pattern] = {
        "email":       re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "phone_kr":    re.compile(r"\b01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}\b"),
        "resident_id": re.compile(r"\b\d{6}[-\s]?[1-4]\d{6}\b"),
        "credit_card": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    }

    def __init__(
        self,
        blocked_patterns: list[str] | None = None,
        detect_pii: bool = False,
        check_target: str = "both",
    ):
        self._patterns: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in (blocked_patterns or [])
        ]
        self._detect_pii = detect_pii
        self._check_target = check_target if check_target in ("input", "output", "both") else "both"

    @property
    def name(self) -> str:
        return "content"

    def configure(self, config: dict[str, Any]) -> None:
        if "blocked_patterns" in config:
            raw = config.get("blocked_patterns") or []
            self._patterns = [re.compile(str(p), re.IGNORECASE) for p in raw]
        if "detect_pii" in config:
            self._detect_pii = bool(config["detect_pii"])
        if "check_target" in config:
            target = str(config["check_target"])
            if target in ("input", "output", "both"):
                self._check_target = target

    def check(self, state: Any) -> GuardResult:
        # 활성화 요소가 하나도 없으면 즉시 통과 — 기본 설정에서 과도한 차단 방지
        if not self._patterns and not self._detect_pii:
            return GuardResult(passed=True, guard_name=self.name)

        targets: list[tuple[str, str]] = []

        if self._check_target in ("output", "both"):
            text = getattr(state, "last_assistant_text", "") or ""
            if text:
                targets.append(("output", text))

        if self._check_target in ("input", "both"):
            msgs = getattr(state, "messages", None) or []
            for m in reversed(msgs):
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
                    if isinstance(content, list):
                        # Anthropic multi-block content
                        content = " ".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    if isinstance(content, str) and content:
                        targets.append(("input", content))
                    break

        if not targets:
            return GuardResult(passed=True, guard_name=self.name)

        for target, text in targets:
            # 사용자 정의 금지 패턴
            for p in self._patterns:
                m = p.search(text)
                if m:
                    snippet = m.group(0)[:20]
                    return GuardResult(
                        passed=False,
                        guard_name=self.name,
                        reason=f"금지 패턴 감지 ({target}): {snippet!r}",
                        severity="block",
                    )
            # PII
            if self._detect_pii:
                for pii_type, p in self._PII_PATTERNS.items():
                    if p.search(text):
                        return GuardResult(
                            passed=False,
                            guard_name=self.name,
                            reason=f"PII 감지 ({target}/{pii_type})",
                            severity="block",
                        )

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
    content_blocked_patterns: list[str] | None = None,
    content_detect_pii: bool = False,
    content_check_target: str = "both",
) -> GuardChain:
    """설정 가능한 가드 체인 생성.

    Args:
        guards: 활성화할 가드 이름 목록. None이면 모든 가드 활성화.
                사용 가능: "iteration", "cost_budget", "token_budget", "content"
        cost_budget_usd: CostBudgetGuard 임계값 (0이면 config/기본값 사용)
        token_budget: TokenBudgetGuard 임계값 (0이면 config/기본값 사용)
        content_blocked_patterns: ContentGuard 금지 정규식 리스트
        content_detect_pii: ContentGuard PII 감지 on/off
        content_check_target: ContentGuard 검사 대상 ('input' | 'output' | 'both')
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
        elif name == "content":
            chain.add(ContentGuard(
                blocked_patterns=content_blocked_patterns,
                detect_pii=content_detect_pii,
                check_target=content_check_target,
            ))
        else:
            chain.add(_GUARD_REGISTRY[name]())

    return chain


def create_default_guard_chain() -> GuardChain:
    """기본 가드 체인 생성 (하위 호환)"""
    return create_guard_chain()
