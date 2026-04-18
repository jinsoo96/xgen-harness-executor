"""
S07 LLM — LLM API 호출

- httpx SSE 스트리밍으로 LLM 호출
- 도구 호출(tool_use) 감지 → pending_tool_calls에 적재
- 재시도 로직 (429: rate limit, 529: overload)
- 모델 폴백 (Anthropic → OpenAI)
- 내부 도구 루프: LLM → tool_use 감지 → Execute 스테이지 → 결과 → LLM 재호출
  (도구 루프는 s08_execute와 협력)
"""

import asyncio
import logging
from typing import Optional

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState, TokenUsage
from ..events.types import MessageEvent, ToolCallEvent, ThinkingEvent
from ..errors import ProviderError, RateLimitError, OverloadError, ContextOverflowError, PipelineAbortError
from ..providers.base import ProviderEvent, ProviderEventType

logger = logging.getLogger("harness.stage.llm")

# 재시도 딜레이 (초)
RETRY_DELAYS = {
    "rate_limit": [10, 20, 40],
    "overload": [1, 2, 4],
    "server": [2, 4, 8],
}
DEFAULT_MAX_RETRIES = 3

# 프로바이더별 컨텍스트 한도 (문자 수 기준, ~4 chars/token 추정)
PROVIDER_CONTEXT_LIMITS = {
    "anthropic": 500_000,
    "openai": 500_000,
    "google": 500_000,
    "bedrock": 500_000,
    "vllm": 50_000,
}


class LLMStage(Stage):
    """LLM API 호출 스테이지"""

    @property
    def stage_id(self) -> str:
        return "s07_llm"

    @property
    def order(self) -> int:
        return 7

    async def execute(self, state: PipelineState) -> dict:
        if not state.provider:
            raise PipelineAbortError("LLM provider not initialized", self.stage_id)

        config = state.config
        call_count = 0
        has_tool_calls = False

        # 단일 LLM 호출 (도구 루프는 Pipeline이 s07→s08→s07 반복으로 처리)
        result_text, tool_calls, usage = await self._call_with_retry(state)
        call_count += 1
        state.llm_call_count += 1

        # 토큰 사용량 업데이트
        state.token_usage += usage
        state.turn_usages.append(usage)
        state.cost_usd += self._estimate_cost(usage, state.provider.model_name)

        # 텍스트 결과
        if result_text:
            state.last_assistant_text = result_text

        # 도구 호출 감지
        if tool_calls:
            has_tool_calls = True
            state.pending_tool_calls = tool_calls
            # assistant 메시지에 텍스트 + tool_use 블록 추가
            content_blocks = []
            if result_text:
                content_blocks.append({"type": "text", "text": result_text})
            for tc in tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["tool_use_id"],
                    "name": tc["tool_name"],
                    "input": tc["tool_input"],
                })
            state.add_message("assistant", content_blocks)
        else:
            # 도구 호출 없으면 텍스트만 추가
            if result_text:
                state.add_message("assistant", result_text)

        # verbose: LLM 응답 완료
        from ..events.types import StageSubstepEvent as _StageSubstep
        await state.emit_verbose(_StageSubstep(
            stage_id=self.stage_id, substep="llm_response_complete",
            meta={"has_tool_calls": has_tool_calls, "text_length": len(result_text),
                  "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        ))

        return {
            "call_count": call_count,
            "has_tool_calls": has_tool_calls,
            "text_length": len(result_text),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }

    async def _call_with_retry(self, state: PipelineState) -> tuple[str, list[dict], TokenUsage]:
        """재시도 로직 포함 LLM 호출"""
        config = state.config
        max_retries = int(self.get_param("max_retries", state, DEFAULT_MAX_RETRIES))
        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                return await self._single_call(state)
            except RateLimitError as e:
                last_error = e
                delay = RETRY_DELAYS["rate_limit"][min(attempt, 2)]
                logger.warning("[LLM] Rate limited, retry %d/%d after %ds", attempt + 1, max_retries, delay)
                await asyncio.sleep(delay)
            except OverloadError as e:
                last_error = e
                delay = RETRY_DELAYS["overload"][min(attempt, 2)]
                logger.warning("[LLM] Overloaded, retry %d/%d after %ds", attempt + 1, max_retries, delay)
                await asyncio.sleep(delay)
            except ProviderError as e:
                if e.recoverable and attempt < max_retries:
                    last_error = e
                    delay = RETRY_DELAYS["server"][min(attempt, 2)]
                    logger.warning("[LLM] Provider error, retry %d/%d after %ds", attempt + 1, max_retries, delay)
                    await asyncio.sleep(delay)
                else:
                    raise

        raise last_error or PipelineAbortError("LLM call failed after retries", self.stage_id)

    async def _single_call(self, state: PipelineState) -> tuple[str, list[dict], TokenUsage]:
        """단일 LLM API 호출"""
        config = state.config
        provider = state.provider

        # stage_params에서 설정 읽기 (UI 설정 > config 글로벌 > stage_config 기본값)
        max_tokens = self.get_param("max_tokens", state, config.max_tokens if config else 8192)
        thinking_enabled = self.get_param("thinking_enabled", state, config.thinking_enabled if config else False)
        thinking_budget = self.get_param("thinking_budget", state, config.thinking_budget_tokens if config else 10000)
        temperature = config.temperature if config else 0.7  # temperature는 글로벌 config에서 (s01_input 스테이지 소관)

        # thinking 설정
        thinking = None
        if thinking_enabled and provider.supports_thinking():
            thinking = {"type": "enabled", "budget_tokens": thinking_budget}

        # 컨텍스트 크기 제한 — 프로바이더별 한도 초과 시 중간 축약
        provider_name = getattr(provider, "provider_name", "") or (state.config.provider if state.config else "")
        context_limit = int(self.get_param(
            "context_limit", state,
            PROVIDER_CONTEXT_LIMITS.get(provider_name, 500_000),
        ))
        state.messages = self._truncate_messages_if_needed(state.messages, context_limit)

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        usage = TokenUsage()

        # verbose: LLM 요청 시작
        from ..events.types import StageSubstepEvent
        import time as _time
        _t_llm = _time.time()
        await state.emit_verbose(StageSubstepEvent(
            stage_id=self.stage_id, substep="llm_request_start",
            meta={"provider": provider.provider_name, "model": provider.model_name,
                  "message_count": len(state.messages),
                  "tools_count": len(state.tool_definitions or [])},
        ))

        async for event in provider.chat(
            messages=state.messages,
            system=state.system_prompt or None,
            tools=state.tool_definitions or None,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            thinking=thinking,
        ):
            if event.type == ProviderEventType.TEXT_DELTA:
                text_parts.append(event.text)
                if state.event_emitter:
                    await state.event_emitter.emit(MessageEvent(text=event.text))

            elif event.type == ProviderEventType.THINKING_DELTA:
                if state.event_emitter:
                    await state.event_emitter.emit(ThinkingEvent(text=event.text))

            elif event.type == ProviderEventType.TOOL_USE:
                tool_calls.append({
                    "tool_use_id": event.tool_use_id,
                    "tool_name": event.tool_name,
                    "tool_input": event.tool_input,
                })
                if state.event_emitter:
                    await state.event_emitter.emit(ToolCallEvent(
                        tool_use_id=event.tool_use_id,
                        tool_name=event.tool_name,
                        tool_input=event.tool_input,
                    ))

            elif event.type == ProviderEventType.USAGE:
                usage.input_tokens += event.input_tokens
                usage.cache_creation_tokens += event.cache_creation_tokens
                usage.cache_read_tokens += event.cache_read_tokens

            elif event.type == ProviderEventType.STOP:
                usage.output_tokens += event.output_tokens

        result_text = "".join(text_parts)
        return result_text, tool_calls, usage

    @staticmethod
    def _truncate_messages_if_needed(messages: list[dict], char_limit: int) -> list[dict]:
        """메시지 총 문자 수가 한도를 초과하면 중간 메시지를 축약한다.

        전략: 앞 40% + 뒤 40% 유지, 중간 20% 제거 후 축약 안내 삽입.
        첫 번째/마지막 메시지는 항상 보존한다.
        """
        import json

        def _msg_chars(msg: dict) -> int:
            content = msg.get("content", "")
            if isinstance(content, str):
                return len(content)
            if isinstance(content, list):
                return sum(
                    len(block.get("text", "")) if isinstance(block, dict) else len(str(block))
                    for block in content
                )
            return len(json.dumps(content, ensure_ascii=False))

        total_chars = sum(_msg_chars(m) for m in messages)
        if total_chars <= char_limit or len(messages) <= 2:
            return messages

        logger.warning(
            "[LLM] Context size %d chars exceeds limit %d, truncating middle messages",
            total_chars, char_limit,
        )

        n = len(messages)
        keep_front = max(1, int(n * 0.4))
        keep_back = max(1, int(n * 0.4))

        # 겹치지 않도록 보정
        if keep_front + keep_back >= n:
            return messages

        front = messages[:keep_front]
        back = messages[-keep_back:]
        removed_count = n - keep_front - keep_back

        truncation_notice = {
            "role": "user",
            "content": (
                f"[System: {removed_count} messages were truncated from the middle of the "
                f"conversation to fit within the context window. "
                f"Original total: {total_chars} chars, limit: {char_limit} chars.]"
            ),
        }

        return front + [truncation_notice] + back

    def _estimate_cost(self, usage: TokenUsage, model: str) -> float:
        """토큰 사용량으로 비용 추정 (USD) — PRICING 단일 진실 소스 사용"""
        from .strategies.token_tracker import PRICING

        # 모델명으로 정확히 매칭, 없으면 부분 매칭
        pricing = PRICING.get(model)
        if not pricing:
            model_lower = model.lower()
            for key, val in PRICING.items():
                if key.lower() in model_lower or model_lower in key.lower():
                    pricing = val
                    break
        if not pricing:
            pricing = {"input": 3.0, "output": 15.0}  # 기본값

        input_cost = usage.input_tokens * pricing["input"] / 1_000_000
        output_cost = usage.output_tokens * pricing["output"] / 1_000_000

        # 캐시 할인
        cache_write_rate = pricing.get("cache_write", pricing["input"] * 1.25)
        cache_read_rate = pricing.get("cache_read", pricing["input"] * 0.1)
        cache_savings = usage.cache_read_tokens * (pricing["input"] - cache_read_rate) / 1_000_000
        return input_cost + output_cost - cache_savings

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("streaming", "SSE 스트리밍 + 재시도 + 폴백", is_default=True),
            StrategyInfo("batch", "비스트리밍 단일 호출"),
        ]
