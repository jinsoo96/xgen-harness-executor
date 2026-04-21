"""ContextCompactor 구현체들"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..interfaces import ContextCompactor, Strategy

logger = logging.getLogger("harness.strategy.compactor")

CHARS_PER_TOKEN = 3


class TokenBudgetCompactor(ContextCompactor):
    """토큰 예산 기반 3단계 압축 — 기본 전략."""

    @property
    def name(self) -> str:
        return "token_budget"

    @property
    def description(self) -> str:
        return "토큰 예산 기반 3단계 압축"

    async def compact(
        self,
        messages: list[dict],
        system_prompt: str,
        budget_tokens: int,
        max_tokens: int,
    ) -> tuple[list[dict], str, bool]:
        available = budget_tokens - max_tokens
        total_chars = len(system_prompt)
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(str(block.get("text", "")))
                        total_chars += len(str(block.get("content", "")))

        estimated = total_chars // CHARS_PER_TOKEN
        budget_used = estimated / available if available > 0 else 1.0
        compacted = False

        # 1단계: 오래된 메시지 제거
        if budget_used > 0.8 and len(messages) > 4:
            messages = [messages[0]] + messages[-3:]
            compacted = True
            logger.info("[Compactor] Kept first + last 3 messages")

        # 2단계: 저우선순위 시스템 프롬프트 섹션 제거
        if budget_used > 0.9 and "<previous_results>" in system_prompt:
            start = system_prompt.find("<previous_results>")
            end = system_prompt.find("</previous_results>")
            if start >= 0 and end >= 0:
                system_prompt = system_prompt[:start] + system_prompt[end + len("</previous_results>"):]
                compacted = True
                logger.info("[Compactor] Removed previous_results section")

        return messages, system_prompt, compacted


class SlidingWindowCompactor(ContextCompactor):
    """슬라이딩 윈도우 — 최근 N개 메시지만 유지."""

    def __init__(self, window_size: int = 10):
        self._window = window_size

    @property
    def name(self) -> str:
        return "sliding_window"

    @property
    def description(self) -> str:
        return f"슬라이딩 윈도우 (최근 {self._window}개 메시지)"

    def configure(self, config: dict) -> None:
        self._window = config.get("window_size", self._window)

    async def compact(
        self,
        messages: list[dict],
        system_prompt: str,
        budget_tokens: int,
        max_tokens: int,
    ) -> tuple[list[dict], str, bool]:
        if len(messages) <= self._window:
            return messages, system_prompt, False
        messages = messages[-self._window:]
        return messages, system_prompt, True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Advanced compactor — state/pd_stores/provider 접근이 필요한 전략
#  (L3 Microcompact · L4 Context Collapse · L5 Autocompact · Cascade)
#
#  v0.11.21 — Code review B+ 지적 "context_collapse_overlay 가 s06 내부 if/elif 라
#  외부 교체 불가" 해소. 여기 정의된 이름이 register_strategy slot="compactor" 로
#  등록되어 UI dropdown/외부 override 경로가 열림.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AdvancedContextCompactor(Strategy, ABC):
    """state 전체를 받아 pd_stores·provider 를 조작하는 compactor.

    기존 `ContextCompactor` 는 (messages, system_prompt, budget, max_tokens) → tuple
    시그니처라 L3/L4/L5 처럼 pd_stores 에 원본 보존하거나 child LLM 을 호출하는
    전략을 표현 못 한다. Advanced 는 state + stage 를 그대로 받아 side-effect 를 일으킨다.

    외부 기여자 훅: 새 `AdvancedContextCompactor` 구현체를 만들고
    `register_strategy("s06_context", "compactor", "<name>", MyCls)` 로 등록하면
    사용자가 `stage_params.s06_context.strategy="<name>"` 로 활성화할 수 있다.
    """

    @abstractmethod
    async def apply(
        self,
        *,
        state: Any,
        stage: Any,
        budget_used: float,
        results: dict,
    ) -> None:
        """state 를 in-place 로 수정하고 results dict 에 요약 필드를 기록."""
        ...


class MicrocompactCompactor(AdvancedContextCompactor):
    """L3 — 오래된 tool_result 블록만 placeholder 로 교체 (비파괴)."""

    @property
    def name(self) -> str:
        return "microcompact"

    @property
    def description(self) -> str:
        return "L3 — 오래된 tool_result 만 placeholder 로 교체 (pd_stores 원본 보존)"

    async def apply(self, *, state, stage, budget_used, results) -> None:
        stage._try_microcompact(state, budget_used, results)


class ContextCollapseOverlayCompactor(AdvancedContextCompactor):
    """L4 — 중간 메시지를 overlay 로 접고 원본은 pd_stores['history'] 에 보존."""

    @property
    def name(self) -> str:
        return "context_collapse_overlay"

    @property
    def description(self) -> str:
        return "L4 — 중간 메시지 overlay 압축 (비파괴, fetch_pd 로 복원 가능)"

    async def apply(self, *, state, stage, budget_used, results) -> None:
        stage._try_context_collapse(state, budget_used, results)


class AutocompactLLMCompactor(AdvancedContextCompactor):
    """L5 — child LLM 9-section 요약으로 교체 (비파괴, 회로 차단)."""

    @property
    def name(self) -> str:
        return "autocompact_llm"

    @property
    def description(self) -> str:
        return "L5 — child LLM 9-section 요약 (비파괴, 실패 3회 시 circuit breaker)"

    async def apply(self, *, state, stage, budget_used, results) -> None:
        await stage._try_autocompact(state, budget_used, results)


class CascadeCompactor(AdvancedContextCompactor):
    """Claude Code cascade — 임계별 L3→L4→L5 자동 에스컬레이션."""

    @property
    def name(self) -> str:
        return "cascade"

    @property
    def description(self) -> str:
        return "임계별 L3→L4→L5 자동 에스컬레이션 (Claude Code 패턴, 한 턴 1 전략)"

    async def apply(self, *, state, stage, budget_used, results) -> None:
        await stage._try_cascade(state, budget_used, results)
