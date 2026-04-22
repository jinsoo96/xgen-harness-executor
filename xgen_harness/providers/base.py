"""
LLMProvider ABC — LLM API 호출 인터페이스

httpx SSE 스트리밍 기반. LangChain/LangGraph 의존성 없음.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Optional


class ProviderEventType(Enum):
    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    TOOL_USE = "tool_use"
    USAGE = "usage"
    STOP = "stop"
    ERROR = "error"


@dataclass
class ProviderEvent:
    """LLM 응답 스트리밍 이벤트"""
    type: ProviderEventType
    text: str = ""
    tool_use_id: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    stop_reason: str = ""
    raw: Optional[dict] = None


class LLMProvider(ABC):
    """LLM 프로바이더 기반 인터페이스"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """프로바이더 이름 (anthropic, openai, google, bedrock)"""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """현재 모델 ID"""
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        stream: bool = True,
        thinking: Optional[dict] = None,
        tool_choice: Optional[str] = None,
    ) -> AsyncGenerator[ProviderEvent, None]:
        """
        LLM API 호출. ProviderEvent를 스트리밍으로 yield.

        Args:
            messages: Anthropic message 포맷 [{"role": "user", "content": "..."}]
            system: 시스템 프롬프트
            tools: 도구 정의 (Anthropic 포맷)
            temperature: 온도
            max_tokens: 최대 토큰
            stream: 스트리밍 여부
            thinking: Extended thinking 설정 {"type": "enabled", "budget_tokens": N}
        """
        ...
        yield  # type: ignore  # make it a generator

    @abstractmethod
    def supports_tool_use(self) -> bool:
        ...

    @abstractmethod
    def supports_thinking(self) -> bool:
        ...

    # v0.11.22 — output_tokens 보정 확장점.
    # 일부 OpenAI 호환 프록시 / vLLM / LangChain adapter 조합에서는 stream 응답의 usage 가
    # 도달하지 않아 `MetricsEvent.output_tokens=0` 이 고정된다. Provider 별로 자기에게
    # 가장 적절한 카운터를 주입할 수 있도록 확장점을 공개한다.
    #   - 기본 구현: chars/3 추정 (영어 ≈ 4, 한국어 ≈ 2. override 로 조정)
    #   - Anthropic: tokenizer endpoint 나 `cl100k_base` 근사
    #   - OpenAI: tiktoken 설치 시 실제 인코딩. 없으면 기본 추정 유지
    # 반환값은 `(tokens, source)` — `source` 는 "usage" | "tiktoken" | "estimate" 같은
    # 메타로 관측자가 metric 에 표시 가능하게.

    def count_tokens(self, text: str) -> tuple[int, str]:
        """`text` 의 토큰 수와 추정 출처를 반환. 기본은 chars/3 휴리스틱.

        override 한 provider 가 실제 tokenizer 를 쓰면 `source="tiktoken"` 등으로 반환.
        빈 문자열은 (0, "empty"). 휴리스틱 하한은 1 토큰.
        """
        if not text:
            return 0, "empty"
        return max(1, len(text) // 3), "estimate_chars_3"


def normalize_base_url(base_url: str, *, api_path: str, version: str = "v1") -> str:
    """LLM provider base_url 을 endpoint 까지 자동 조립.

    "<base>" / "<base>/v1" / "<base>/v1/<api_path>" 모두 같은 결과로 정규화.
    예) normalize_base_url("https://api.openai.com/v1", api_path="chat/completions")
        → "https://api.openai.com/v1/chat/completions"
    """
    base = (base_url or "").rstrip("/")
    suffix = f"/{api_path}"
    versioned_suffix = f"/{version}/{api_path}"
    if base.endswith(versioned_suffix):
        return base
    if base.endswith(f"/{version}"):
        return base + suffix
    if base.endswith(suffix):
        return base
    return base + versioned_suffix
