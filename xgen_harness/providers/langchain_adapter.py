"""
LangChain Provider Adapter — xgen의 LangChain LLM을 하네스에 끼우는 어댑터

xgen-workflow는 LangChain BaseChatModel을 사용한다:
  - ChatAnthropic, ChatOpenAI, ChatGoogleGenerativeAI, ChatBedrockConverse

이 어댑터는 LangChain 모델을 하네스 LLMProvider 인터페이스로 래핑하여
하네스 파이프라인이 xgen의 LLM을 그대로 쓸 수 있게 한다.

사용:
    # xgen에서 이미 만든 LLM 인스턴스
    from langchain_anthropic import ChatAnthropic
    llm = ChatAnthropic(model="claude-sonnet-4-20250514", ...)

    # 하네스에 끼우기
    from xgen_harness.providers.langchain_adapter import LangChainAdapter
    provider = LangChainAdapter(llm)
    state.provider = provider  # 파이프라인이 이걸 사용

    # 또는 register_provider로 등록
    from xgen_harness.providers import register_provider
    register_provider("langchain", lambda api_key, model, base_url: LangChainAdapter(existing_llm))
"""

import json
import logging
from typing import Any, AsyncGenerator, Optional

from .base import LLMProvider, ProviderEvent, ProviderEventType

logger = logging.getLogger("harness.providers.langchain")


class LangChainAdapter(LLMProvider):
    """LangChain BaseChatModel → 하네스 LLMProvider 어댑터.

    xgen이 이미 생성한 LangChain LLM 인스턴스를 받아서
    하네스 파이프라인의 ProviderEvent 스트리밍으로 변환한다.

    Args:
        llm: LangChain BaseChatModel 인스턴스 (ChatOpenAI, ChatAnthropic 등)
        provider_name_override: 프로바이더 이름 오버라이드 (자동 감지 안 될 때)
    """

    def __init__(self, llm, provider_name_override: str = ""):
        self._llm = llm
        self._provider_override = provider_name_override
        self._model = getattr(llm, "model_name", getattr(llm, "model", "unknown"))

    @property
    def provider_name(self) -> str:
        if self._provider_override:
            return self._provider_override
        # LangChain 클래스명에서 프로바이더 추론
        cls_name = type(self._llm).__name__.lower()
        if "anthropic" in cls_name:
            return "anthropic"
        if "google" in cls_name or "gemini" in cls_name:
            return "google"
        if "bedrock" in cls_name:
            return "bedrock"
        return "openai"  # ChatOpenAI 포함 기본값

    @property
    def model_name(self) -> str:
        return self._model

    def supports_tool_use(self) -> bool:
        return hasattr(self._llm, "bind_tools")

    def supports_thinking(self) -> bool:
        # Anthropic만 extended thinking 지원
        return "anthropic" in self.provider_name

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
        """LangChain astream_events를 ProviderEvent로 변환."""

        # LangChain 메시지 포맷으로 변환
        lc_messages = self._to_langchain_messages(messages, system)

        # 도구 바인딩
        llm = self._llm
        if tools:
            lc_tools = self._to_langchain_tools(tools)
            if lc_tools and hasattr(llm, "bind_tools"):
                llm = llm.bind_tools(lc_tools)

        # 스트리밍 실행
        total_text = ""
        try:
            async for chunk in llm.astream(lc_messages):
                # AIMessageChunk에서 이벤트 추출
                content = getattr(chunk, "content", "")

                # 텍스트 델타
                if isinstance(content, str) and content:
                    total_text += content
                    yield ProviderEvent(
                        type=ProviderEventType.TEXT_DELTA,
                        text=content,
                    )
                elif isinstance(content, list):
                    # Anthropic 스타일 content blocks
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text" and block.get("text"):
                                total_text += block["text"]
                                yield ProviderEvent(
                                    type=ProviderEventType.TEXT_DELTA,
                                    text=block["text"],
                                )
                            elif block.get("type") == "tool_use":
                                yield ProviderEvent(
                                    type=ProviderEventType.TOOL_USE,
                                    tool_use_id=block.get("id", ""),
                                    tool_name=block.get("name", ""),
                                    tool_input=block.get("input", {}),
                                )

                # 도구 호출 (OpenAI 스타일) — Anthropic content blocks에서 이미 처리된 경우 스킵
                tool_calls = getattr(chunk, "tool_calls", None)
                if tool_calls and not isinstance(content, list):
                    for tc in tool_calls:
                        yield ProviderEvent(
                            type=ProviderEventType.TOOL_USE,
                            tool_use_id=tc.get("id", ""),
                            tool_name=tc.get("name", ""),
                            tool_input=tc.get("args", {}),
                        )

                # usage_metadata (LangChain 0.2+)
                usage = getattr(chunk, "usage_metadata", None)
                if usage:
                    yield ProviderEvent(
                        type=ProviderEventType.USAGE,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                    )

            # 스트림 종료
            yield ProviderEvent(
                type=ProviderEventType.STOP,
                stop_reason="end_turn",
            )

        except Exception as e:
            logger.error("[LangChainAdapter] Error: %s", e)
            yield ProviderEvent(
                type=ProviderEventType.ERROR,
                text=str(e),
            )

    def _to_langchain_messages(self, messages: list[dict], system: Optional[str]) -> list:
        """Anthropic 포맷 메시지 → LangChain 메시지로 변환."""
        from collections import namedtuple

        # 간단한 메시지 튜플 (LangChain이 tuple도 받음)
        lc_msgs = []

        if system:
            lc_msgs.append(("system", system))

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "assistant":
                # tool_use 블록이 있으면 텍스트만 추출
                if isinstance(content, list):
                    texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    content = "\n".join(texts)
                lc_msgs.append(("assistant", content or ""))
            elif role == "user":
                if isinstance(content, list):
                    # tool_result 블록 → 텍스트 결합
                    parts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "tool_result":
                                parts.append(f"[Tool Result: {block.get('tool_use_id', '')}]\n{block.get('content', '')}")
                            elif block.get("type") == "text":
                                parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            parts.append(block)
                    content = "\n".join(parts)
                lc_msgs.append(("human", content))
            else:
                lc_msgs.append((role, str(content)))

        return lc_msgs

    def _to_langchain_tools(self, tools: list[dict]) -> list[dict]:
        """Anthropic 도구 포맷 → LangChain 도구 포맷."""
        lc_tools = []
        for tool in tools:
            # Anthropic: {"name": "...", "description": "...", "input_schema": {...}}
            # LangChain bind_tools: {"name": "...", "description": "...", "parameters": {...}}
            name = tool.get("name", "")
            if not name:
                fn = tool.get("function", {})
                name = fn.get("name", "")
                desc = fn.get("description", "")
                schema = fn.get("input_schema", fn.get("parameters", {}))
            else:
                desc = tool.get("description", "")
                schema = tool.get("input_schema", {})

            if name:
                lc_tools.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": schema,
                    },
                })
        return lc_tools
