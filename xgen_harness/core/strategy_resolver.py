"""
StrategyResolver — Strategy 이름 → 구현체 인스턴스 매핑

geny-harness의 Stage×Strategy 이중 추상화를 Python으로 구현.
각 스테이지가 선언한 Strategy 이름을 실제 클래스로 해석.

사용:
    resolver = StrategyResolver.default()
    retry = resolver.resolve("s07_llm", "retry", "exponential_backoff")
"""

import logging
from typing import Any, Optional, Type

from ..stages.interfaces import Strategy

logger = logging.getLogger("harness.strategy_resolver")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  전역 레지스트리: (stage_id, slot_name, impl_name) → Strategy 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_REGISTRY: dict[tuple[str, str, str], Type[Strategy]] = {}
_DEFAULTS_REGISTERED = False


def register_strategy(stage_id: str, slot_name: str, impl_name: str, cls: Type[Strategy]) -> None:
    _REGISTRY[(stage_id, slot_name, impl_name)] = cls


def _ensure_defaults_registered() -> None:
    """기본 Strategy 가 한 번이라도 등록되지 않았으면 트리거."""
    global _DEFAULTS_REGISTERED
    if _DEFAULTS_REGISTERED:
        return
    _DEFAULTS_REGISTERED = True
    try:
        _register_defaults()
    except Exception:
        # 등록 실패해도 레지스트리 자체는 사용 가능해야 함
        pass


class StrategyResolver:
    """Strategy 이름을 구현체 인스턴스로 해석."""

    def __init__(self):
        self._cache: dict[tuple[str, str, str], Strategy] = {}

    def resolve(
        self,
        stage_id: str,
        slot_name: str,
        impl_name: str,
        config: Optional[dict[str, Any]] = None,
    ) -> Optional[Strategy]:
        """Strategy 인스턴스 반환. 캐시 사용."""
        _ensure_defaults_registered()
        key = (stage_id, slot_name, impl_name)

        # 캐시 히트
        if key in self._cache:
            return self._cache[key]

        # 레지스트리에서 클래스 조회
        cls = _REGISTRY.get(key)

        # stage_id 와일드카드 폴백 (공통 전략)
        if cls is None:
            cls = _REGISTRY.get(("*", slot_name, impl_name))

        if cls is None:
            logger.warning("Strategy not found: %s/%s/%s", stage_id, slot_name, impl_name)
            return None

        instance = cls()
        if config:
            instance.configure(config)

        self._cache[key] = instance
        return instance

    @classmethod
    def default(cls) -> "StrategyResolver":
        """기본 Strategy가 등록된 Resolver 생성."""
        _register_defaults()
        return cls()


def _register_defaults() -> None:
    """모든 기본 Strategy를 레지스트리에 등록."""
    if _REGISTRY:
        return  # 이미 등록됨

    from ..stages.strategies import (
        ExponentialBackoffRetry, NoRetry,
        CompositeToolRouter, MCPToolRouter, BuiltinToolRouter,
        SequentialToolExecutor,
        LLMJudgeEvaluation, RuleBasedEvaluation, NoValidation,
        WeightedScorer,
        ProgressiveDiscovery, EagerLoadDiscovery,
        TokenBudgetCompactor, SlidingWindowCompactor,
    )
    from ..stages.strategies.tool_executor import ParallelToolExecutor

    # s04_tool_index — discovery
    register_strategy("s04_tool_index", "discovery", "progressive_3level", ProgressiveDiscovery)
    register_strategy("s04_tool_index", "discovery", "eager_load", EagerLoadDiscovery)

    # s06_context — compactor
    register_strategy("s06_context", "compactor", "token_budget", TokenBudgetCompactor)
    register_strategy("s06_context", "compactor", "sliding_window", SlidingWindowCompactor)

    # s07_llm — retry
    register_strategy("s07_llm", "retry", "exponential_backoff", ExponentialBackoffRetry)
    register_strategy("s07_llm", "retry", "no_retry", NoRetry)

    # s08_execute — executor, router
    register_strategy("s08_execute", "executor", "sequential", SequentialToolExecutor)
    register_strategy("s08_execute", "executor", "parallel", ParallelToolExecutor)
    register_strategy("s08_execute", "router", "composite", CompositeToolRouter)
    register_strategy("s08_execute", "router", "mcp", MCPToolRouter)
    register_strategy("s08_execute", "router", "builtin", BuiltinToolRouter)

    # s09_validate — evaluation
    register_strategy("s09_validate", "evaluation", "llm_judge", LLMJudgeEvaluation)
    register_strategy("s09_validate", "evaluation", "rule_based", RuleBasedEvaluation)
    register_strategy("s09_validate", "evaluation", "none", NoValidation)

    # s10_decide — decide (간단한 내장 Strategy)
    from ..stages.strategies._decide import ThresholdDecide, AlwaysPassDecide
    register_strategy("s10_decide", "decide", "threshold", ThresholdDecide)
    register_strategy("s10_decide", "decide", "always_pass", AlwaysPassDecide)

    # s03 — cache
    from ..stages.strategies.cache import AnthropicCacheStrategy, NoCacheStrategy
    register_strategy("s03_system_prompt", "cache", "anthropic_cache", AnthropicCacheStrategy)
    register_strategy("s03_system_prompt", "cache", "no_cache", NoCacheStrategy)

    # s07 — token tracker, thinking processor, response parser
    from ..stages.strategies.token_tracker import DefaultTokenTracker, ModelPricingCalculator
    register_strategy("s07_llm", "token_tracker", "default", DefaultTokenTracker)
    register_strategy("s07_llm", "cost_calculator", "model_pricing", ModelPricingCalculator)

    from ..stages.strategies.thinking import DefaultThinkingProcessor, NoThinkingProcessor
    register_strategy("s07_llm", "thinking", "default", DefaultThinkingProcessor)
    register_strategy("s07_llm", "thinking", "disabled", NoThinkingProcessor)

    from ..stages.strategies.parser import AnthropicResponseParser, OpenAIResponseParser, DefaultCompletionDetector
    register_strategy("s07_llm", "parser", "anthropic", AnthropicResponseParser)
    register_strategy("s07_llm", "parser", "openai", OpenAIResponseParser)
    register_strategy("s07_llm", "completion_detector", "default", DefaultCompletionDetector)

    # 공통 — scorer (와일드카드)
    register_strategy("*", "scorer", "weighted", WeightedScorer)

    logger.debug("Registered %d strategies", len(_REGISTRY))
