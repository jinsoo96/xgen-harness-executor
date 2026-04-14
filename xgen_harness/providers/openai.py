"""
OpenAI LLM Provider — httpx SSE 스트리밍

OpenAI Chat Completions API. tool_use를 Anthropic 포맷으로 통합 변환.
"""

import json
import logging
from typing import Any, AsyncGenerator, Optional

import httpx

from .base import LLMProvider, ProviderEvent, ProviderEventType
from ..errors import ProviderError

logger = logging.getLogger("harness.provider.openai")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(LLMProvider):
    """OpenAI API 프로바이더"""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: Optional[str] = None):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url or OPENAI_API_URL

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    def supports_tool_use(self) -> bool:
        return True

    def supports_thinking(self) -> bool:
        return False

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
        # Anthropic 메시지 포맷 → OpenAI 포맷 변환
        oai_messages = _convert_messages(messages, system)
        oai_tools = _convert_tools(tools) if tools else None

        body: dict[str, Any] = {
            "model": self._model,
            "messages": oai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if oai_tools:
            body["tools"] = oai_tools

        if stream:
            body["stream_options"] = {"include_usage": True}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        if stream:
            async for event in self._stream_request(body, headers):
                yield event
        else:
            event = await self._batch_request(body, headers)
            yield event

    async def _stream_request(
        self, body: dict, headers: dict
    ) -> AsyncGenerator[ProviderEvent, None]:
        current_tool_calls: dict[int, dict] = {}

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

                    # usage 이벤트 (stream_options)
                    usage = data.get("usage")
                    if usage:
                        yield ProviderEvent(
                            type=ProviderEventType.USAGE,
                            input_tokens=usage.get("prompt_tokens", 0),
                            output_tokens=usage.get("completion_tokens", 0),
                        )

                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})
                    finish_reason = choices[0].get("finish_reason")

                    # 텍스트 델타
                    content = delta.get("content")
                    if content:
                        yield ProviderEvent(
                            type=ProviderEventType.TEXT_DELTA,
                            text=content,
                        )

                    # 도구 호출 델타
                    tool_calls = delta.get("tool_calls", [])
                    for tc in tool_calls:
                        idx = tc.get("index", 0)
                        if idx not in current_tool_calls:
                            current_tool_calls[idx] = {
                                "id": tc.get("id", ""),
                                "name": tc.get("function", {}).get("name", ""),
                                "arguments": "",
                            }
                        else:
                            args = tc.get("function", {}).get("arguments", "")
                            current_tool_calls[idx]["arguments"] += args

                    if finish_reason:
                        # 도구 호출 완료 시 emit
                        for tc_data in current_tool_calls.values():
                            try:
                                parsed = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                            except json.JSONDecodeError:
                                parsed = {"raw": tc_data["arguments"]}
                            yield ProviderEvent(
                                type=ProviderEventType.TOOL_USE,
                                tool_use_id=tc_data["id"],
                                tool_name=tc_data["name"],
                                tool_input=parsed,
                            )
                        current_tool_calls.clear()

                        yield ProviderEvent(
                            type=ProviderEventType.STOP,
                            stop_reason=finish_reason,
                        )

    async def _batch_request(self, body: dict, headers: dict) -> ProviderEvent:
        body["stream"] = False
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            response = await client.post(self._base_url, json=body, headers=headers)
            if response.status_code != 200:
                raise ProviderError.from_status(response.status_code, response.text)

            data = response.json()
            choice = data["choices"][0]
            message = choice.get("message", {})
            usage = data.get("usage", {})

            return ProviderEvent(
                type=ProviderEventType.STOP,
                text=message.get("content", ""),
                stop_reason=choice.get("finish_reason", ""),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                raw=data,
            )


def _convert_messages(messages: list[dict], system: Optional[str] = None) -> list[dict]:
    """Anthropic 메시지 포맷 → OpenAI 포맷"""
    oai_msgs = []
    if system:
        oai_msgs.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            oai_msgs.append({"role": role, "content": content})
        elif isinstance(content, list):
            # tool_result 리스트 → OpenAI tool 메시지로 변환
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    oai_msgs.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": str(block.get("content", "")),
                    })
                elif isinstance(block, dict) and block.get("type") == "text":
                    oai_msgs.append({"role": role, "content": block.get("text", "")})
                else:
                    oai_msgs.append({"role": role, "content": str(block)})
        else:
            oai_msgs.append({"role": role, "content": str(content)})

    return oai_msgs


def _convert_tools(tools: list[dict]) -> list[dict]:
    """Anthropic tool 정의 → OpenAI function 정의"""
    oai_tools = []
    for tool in tools:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return oai_tools
