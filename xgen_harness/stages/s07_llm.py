"""
S07 LLM — LLM API 호출

- httpx SSE 스트리밍으로 LLM 호출
- 도구 호출(tool_use) 감지 → pending_tool_calls에 적재
- 재시도 로직 (429: rate limit, 529: overload)
- 모델 폴백 (Anthropic → OpenAI)
- 내부 도구 루프: LLM → tool_use 감지 → Execute 스테이지 → 결과 → LLM 재호출
  (도구 루프는 s08_act와 협력)
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


class LLMStage(Stage):
    """LLM API 호출 스테이지"""

    @property
    def stage_id(self) -> str:
        return "s07_llm"

    @property
    def order(self) -> int:
        return 7

    async def execute(self, state: PipelineState) -> dict:
        # v0.9.0+: provider 생성 책임이 s07 로 이관됨 (PHILOSOPHY §2 s07 "담당").
        # state.provider 가 아직 없으면 lazy init — s01_input 이 아닌 여기서 생성.
        # backward compat: s01_input 이 이미 생성해 뒀다면 그대로 재사용.
        if not state.provider:
            await self._lazy_init_provider(state)
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

    async def _lazy_init_provider(self, state: PipelineState) -> None:
        """state.provider 가 아직 없으면 여기서 생성 (v0.9.0+).

        PHILOSOPHY §2 s07 "담당": provider 생성 + API key 해석 + base_url 해석.
        s01_input 이 backward-compat 으로 여전히 먼저 생성할 수 있지만, 향후
        s01 이 입력 정규화만 담당하게 되면 s07 가 단독 주체가 된다.
        """
        import os
        from ..core.execution_context import get_api_key as ctx_get_api_key
        from ..providers import (
            create_provider, get_api_key_env, resolve_api_key_from_file,
            PROVIDER_DEFAULT_MODEL,
        )

        config = state.config
        if not config:
            raise PipelineAbortError("Config not set", self.stage_id)

        provider_name: str = (config.provider or "").lower()
        model_name: str = config.model or PROVIDER_DEFAULT_MODEL.get(provider_name, "")
        if not provider_name or not model_name:
            raise PipelineAbortError(
                f"Provider/model not resolved (provider={provider_name!r}, model={model_name!r})",
                self.stage_id,
            )

        # API key: ExecutionContext → ServiceProvider → env → file
        api_key: Optional[str] = ctx_get_api_key()
        if not api_key:
            services = state.metadata.get("services")
            if services and getattr(services, "config", None):
                try:
                    api_key = await services.config.get_api_key(provider_name)
                except Exception as e:
                    logger.debug("[LLM] ServiceProvider API key lookup failed: %s", e)
        if not api_key:
            env_var = get_api_key_env(provider_name)
            api_key = os.environ.get(env_var, "")
            if not api_key:
                # 파일 폴백 — providers 레지스트리 헬퍼 위임
                # (경로 고정 금지: XGEN_HARNESS_API_KEY_FILE_DIR 로 override 가능)
                api_key = resolve_api_key_from_file(provider_name)
        if not api_key:
            raise PipelineAbortError(
                f"{provider_name} API key not configured", self.stage_id,
            )

        # base_url: ServiceProvider(Redis) → env → None
        base_url: Optional[str] = None
        env_var_url = f"{provider_name.upper()}_API_BASE_URL"
        services = state.metadata.get("services")
        if services and getattr(services, "config", None):
            try:
                get_setting = getattr(services.config, "get_setting", None)
                if get_setting is not None:
                    base_url = await get_setting(env_var_url) or None
                else:
                    base_url = await services.config.get_value(env_var_url, "") or None
            except Exception as e:
                logger.debug("[LLM] base_url Redis 조회 실패: %s", e)
        if not base_url:
            base_url = os.environ.get(env_var_url, "") or None

        state.provider = create_provider(provider_name, api_key, model_name, base_url=base_url)
        logger.info("[LLM] lazy init provider=%s, model=%s", provider_name, model_name)

    async def _call_with_retry(self, state: PipelineState) -> tuple[str, list[dict], TokenUsage]:
        """재시도 로직 포함 LLM 호출.

        딜레이는 stage_params override 가능:
          retry_delays_rate_limit: list[int] (초)
          retry_delays_overload:   list[int]
          retry_delays_server:     list[int]
        """
        max_retries = int(self.get_param("max_retries", state, DEFAULT_MAX_RETRIES))
        delays = {
            "rate_limit": list(self.get_param("retry_delays_rate_limit", state, RETRY_DELAYS["rate_limit"])),
            "overload":   list(self.get_param("retry_delays_overload",   state, RETRY_DELAYS["overload"])),
            "server":     list(self.get_param("retry_delays_server",     state, RETRY_DELAYS["server"])),
        }

        def _pick(kind: str, attempt: int) -> int:
            seq = delays[kind] or RETRY_DELAYS[kind]
            return int(seq[min(attempt, len(seq) - 1)])

        last_error: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return await self._single_call(state)
            except RateLimitError as e:
                last_error = e
                delay = _pick("rate_limit", attempt)
                logger.warning("[LLM] Rate limited, retry %d/%d after %ds", attempt + 1, max_retries, delay)
                await asyncio.sleep(delay)
            except OverloadError as e:
                last_error = e
                delay = _pick("overload", attempt)
                logger.warning("[LLM] Overloaded, retry %d/%d after %ds", attempt + 1, max_retries, delay)
                await asyncio.sleep(delay)
            except ProviderError as e:
                if e.recoverable and attempt < max_retries:
                    last_error = e
                    delay = _pick("server", attempt)
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

        # 컨텍스트 크기 제한 — 프로바이더별 한도 초과 시 중간 축약.
        # 레지스트리(providers.get_context_limit) 단일 조회 — 하드코딩 금지.
        from ..providers import get_context_limit
        provider_name = getattr(provider, "provider_name", "") or (state.config.provider if state.config else "")
        context_limit = int(self.get_param(
            "context_limit", state, get_context_limit(provider_name),
        ))
        state.messages = self._truncate_messages_if_needed(state.messages, context_limit)

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        usage = TokenUsage()
        # v0.11.21 — MetricsEvent.output_tokens=0 현상 해결.
        # Anthropic 은 message_delta(STOP) 에 output_tokens 를 싣고,
        # OpenAI 는 stream_options.include_usage 로 USAGE 이벤트 말미에 completion_tokens 를 싣는다.
        # 둘 중 먼저 도착한 값만 집계(중복 방지).
        _output_tokens_recorded = False

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

        # v0.11.19/20 — tool_choice: s04 가 state.metadata["force_tool_choice"] 에 세팅.
        # 가능 값: "auto" (기본), "required" (반드시 tool 하나 호출), "none", 또는 특정 tool 이름.
        #
        # v0.11.20 circuit breaker: "required" 는 루프 첫 iter 에만 강제, 2회차부터 "auto" 로 격하.
        # 이유: OpenAI required 는 매 호출마다 tool 호출 강제이므로 text 응답 생성 불가 → 무한 루프.
        # 사용자 의도(첫 탐색 강제)는 지키면서 후속 iter 는 LLM 판단에 맡긴다.
        _tool_choice = None
        if state.tool_definitions:
            _tool_choice = (state.metadata or {}).get("force_tool_choice")
            if _tool_choice == "required" and getattr(state, "loop_iteration", 0) >= 1:
                # loop_iteration 은 s05_strategy 에서 1-based 로 증가. 첫 iter = 0 or 1 환경에 무관하게
                # 1 번째 LLM 호출 직후 (loop_iteration >= 1) 부터 auto 로 격하.
                logger.info(
                    "[LLM] force_tool_choice=required → auto (iter=%d, circuit breaker)",
                    getattr(state, "loop_iteration", 0),
                )
                _tool_choice = "auto"
        elif (state.metadata or {}).get("force_tool_choice"):
            logger.warning(
                "[LLM] force_tool_choice set but no tool_definitions — ignored"
            )
        async for event in provider.chat(
            messages=state.messages,
            system=state.system_prompt or None,
            tools=state.tool_definitions or None,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            thinking=thinking,
            tool_choice=_tool_choice,
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
                # OpenAI 경로: USAGE 이벤트가 completion_tokens 를 싣는다. 선착 집계.
                if event.output_tokens and not _output_tokens_recorded:
                    usage.output_tokens += event.output_tokens
                    _output_tokens_recorded = True

            elif event.type == ProviderEventType.STOP:
                # Anthropic 경로: message_delta 이벤트가 output_tokens 누적값 전달.
                if event.output_tokens and not _output_tokens_recorded:
                    usage.output_tokens += event.output_tokens
                    _output_tokens_recorded = True

        result_text = "".join(text_parts)

        # v0.11.22 — output_tokens 보정 fallback.
        # 일부 OpenAI 호환 엔드포인트(vLLM / 사내 프록시 / LangChain adapter 일부)가
        # stream 응답에 usage payload 를 포함하지 않아 `_output_tokens_recorded=False` 로 끝난다.
        # 이 때 provider.count_tokens 확장점을 호출해 보정. 기본 구현은 chars/3 휴리스틱이고,
        # OpenAI provider 는 tiktoken 이 깔려 있으면 실제 인코딩으로 계산.
        # 보정이 수행되면 state.metadata 에 출처(source)를 남겨 리포트에서 추적 가능.
        if not _output_tokens_recorded and result_text:
            try:
                estimated, source = provider.count_tokens(result_text)
            except Exception as e:
                logger.warning("[LLM] provider.count_tokens fallback 실패: %s", e)
                estimated, source = 0, "failed"
            if estimated > 0:
                usage.output_tokens = estimated
                # 관측자가 추정인지 실제인지 구분할 수 있도록 flag.
                state.metadata.setdefault("output_tokens_sources", []).append(source)
                logger.info("[LLM] output_tokens=%d (source=%s, fallback)", estimated, source)

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
