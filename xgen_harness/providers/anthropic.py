"""
Anthropic LLM Provider — httpx SSE 스트리밍

공식 SDK 없이 httpx로 직접 Messages API 호출.
prompt caching, extended thinking, tool use 지원.
"""

import json
import logging
from typing import Any, AsyncGenerator, Optional

import httpx

from .base import LLMProvider, ProviderEvent, ProviderEventType
from ..errors import ProviderError, RateLimitError, OverloadError

logger = logging.getLogger("harness.provider.anthropic")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API 프로바이더"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514", base_url: Optional[str] = None):
        self._api_key = api_key
        self._model = model
        self._base_url = (base_url or ANTHROPIC_API_URL).rstrip("/")
        if not self._base_url.endswith("/v1/messages"):
            if self._base_url.endswith("/v1"):
                self._base_url += "/messages"
            elif not self._base_url.endswith("/messages"):
                self._base_url += "/v1/messages"

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model

    def supports_tool_use(self) -> bool:
        return True

    def supports_thinking(self) -> bool:
        return "claude" in self._model.lower()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        stream: bool = True,
        thinking: Optional[dict] = None,
    ) -> AsyncGenerator[ProviderEvent, None]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        # thinking 모드에서는 temperature 설정 불가 (Anthropic 제약)
        if thinking and thinking.get("type") == "enabled":
            body["thinking"] = thinking
        else:
            body["temperature"] = temperature

        if system:
            body["system"] = system

        if tools:
            body["tools"] = tools

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }

        # prompt caching 활성화
        if tools or system:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        if stream:
            async for event in self._stream_request(body, headers):
                yield event
        else:
            event = await self._batch_request(body, headers)
            yield event

    async def _stream_request(
        self, body: dict, headers: dict
    ) -> AsyncGenerator[ProviderEvent, None]:
        """SSE 스트리밍 요청"""
        # 현재 도구 호출 상태 추적
        current_tool_id = ""
        current_tool_name = ""
        tool_input_json = ""

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            async with client.stream("POST", self._base_url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise ProviderError.from_status(response.status_code, error_body.decode())

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type", "")

                    if event_type == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool_id = block.get("id", "")
                            current_tool_name = block.get("name", "")
                            tool_input_json = ""

                    elif event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        delta_type = delta.get("type", "")

                        if delta_type == "text_delta":
                            yield ProviderEvent(
                                type=ProviderEventType.TEXT_DELTA,
                                text=delta.get("text", ""),
                            )
                        elif delta_type == "thinking_delta":
                            yield ProviderEvent(
                                type=ProviderEventType.THINKING_DELTA,
                                text=delta.get("thinking", ""),
                            )
                        elif delta_type == "input_json_delta":
                            tool_input_json += delta.get("partial_json", "")

                    elif event_type == "content_block_stop":
                        if current_tool_name:
                            try:
                                parsed_input = json.loads(tool_input_json) if tool_input_json else {}
                            except json.JSONDecodeError:
                                parsed_input = {"raw": tool_input_json}

                            yield ProviderEvent(
                                type=ProviderEventType.TOOL_USE,
                                tool_use_id=current_tool_id,
                                tool_name=current_tool_name,
                                tool_input=parsed_input,
                            )
                            current_tool_id = ""
                            current_tool_name = ""
                            tool_input_json = ""

                    elif event_type == "message_delta":
                        delta = data.get("delta", {})
                        usage = data.get("usage", {})
                        yield ProviderEvent(
                            type=ProviderEventType.STOP,
                            stop_reason=delta.get("stop_reason", ""),
                            output_tokens=usage.get("output_tokens", 0),
                        )

                    elif event_type == "message_start":
                        usage = data.get("message", {}).get("usage", {})
                        if usage:
                            yield ProviderEvent(
                                type=ProviderEventType.USAGE,
                                input_tokens=usage.get("input_tokens", 0),
                                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                            )

    async def _batch_request(self, body: dict, headers: dict) -> ProviderEvent:
        """비스트리밍 요청"""
        body["stream"] = False
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            response = await client.post(self._base_url, json=body, headers=headers)

            if response.status_code != 200:
                raise ProviderError.from_status(response.status_code, response.text)

            data = response.json()
            text_parts = []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

            usage = data.get("usage", {})
            return ProviderEvent(
                type=ProviderEventType.STOP,
                text="\n".join(text_parts),
                stop_reason=data.get("stop_reason", ""),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                raw=data,
            )
