"""
Token tracking + Cost calculation strategies

geny-harness s07_token 차용:
  TokenTracker: API 응답에서 토큰 사용량 추적
  CostCalculator: 프로바이더/모델별 비용 계산

기존 s07_llm에 하드코딩되어 있던 토큰/비용 로직을 Strategy로 분리.
프로바이더별 가격 모델이 다를 때 유연하게 대응.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..interfaces import Strategy

logger = logging.getLogger("harness.strategy.token")


@dataclass
class TokenUsageRecord:
    """토큰 사용 기록"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class TokenTracker(Strategy, ABC):
    """토큰 사용량 추적 인터페이스"""

    @abstractmethod
    def track(self, api_response: dict, state: Any) -> TokenUsageRecord:
        """API 응답에서 토큰 사용량 추출"""
        ...


class CostCalculator(Strategy, ABC):
    """비용 계산 인터페이스"""

    @abstractmethod
    def calculate(self, usage: TokenUsageRecord, model: str) -> float:
        """토큰 사용량 → USD 비용"""
        ...


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  구현체
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DefaultTokenTracker(TokenTracker):
    """기본 토큰 추적 — Anthropic/OpenAI 공통"""

    @property
    def name(self) -> str:
        return "default"

    def track(self, api_response: dict, state: Any) -> TokenUsageRecord:
        usage = api_response.get("usage", {})
        return TokenUsageRecord(
            input_tokens=usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            output_tokens=usage.get("output_tokens", usage.get("completion_tokens", 0)),
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
        )


# 가격 테이블 (USD per 1M tokens)
PRICING = {
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-haiku-4-20250414": {"input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "o3-mini": {"input": 1.10, "output": 4.40},
    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


class ModelPricingCalculator(CostCalculator):
    """모델별 가격 테이블 기반 비용 계산"""

    @property
    def name(self) -> str:
        return "model_pricing"

    def calculate(self, usage: TokenUsageRecord, model: str) -> float:
        pricing = PRICING.get(model)
        if not pricing:
            # 알 수 없는 모델 — 보수적 추정 ($3/1M input, $15/1M output)
            pricing = {"input": 3.0, "output": 15.0}

        cost = 0.0
        cost += (usage.input_tokens / 1_000_000) * pricing.get("input", 3.0)
        cost += (usage.output_tokens / 1_000_000) * pricing.get("output", 15.0)
        cost += (usage.cache_creation_tokens / 1_000_000) * pricing.get("cache_write", pricing.get("input", 3.0) * 1.25)
        cost += (usage.cache_read_tokens / 1_000_000) * pricing.get("cache_read", pricing.get("input", 3.0) * 0.1)

        return cost
