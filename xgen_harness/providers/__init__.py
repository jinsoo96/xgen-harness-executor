"""
LLM Providers — 프로바이더 레지스트리 + 팩토리

새 프로바이더 추가:
    from xgen_harness.providers import register_provider
    register_provider("bedrock", BedrockProvider)

사용:
    from xgen_harness.providers import create_provider
    provider = create_provider("anthropic", api_key, model)
"""

import logging
import os
from typing import Optional, Type

from .base import LLMProvider, ProviderEvent, ProviderEventType

logger = logging.getLogger("harness.providers")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  프로바이더 레지스트리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_REGISTRY: dict[str, Type[LLMProvider]] = {}

# API 키 환경변수 매핑 — 단일 진실 소스
PROVIDER_API_KEY_MAP: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "bedrock": "AWS_ACCESS_KEY_ID",
    "vllm": "VLLM_API_KEY",
}

# 프로바이더별 기본 모델
PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.0-flash",
}

# 프로바이더별 추가 모델 목록 — UI 드롭다운 동적 렌더용.
# 기본 모델(PROVIDER_DEFAULT_MODEL)은 자동으로 맨 앞에 포함됨.
# 새 provider 등록 시 이 dict 에 append → stage_config / harness.py 가 자동 반영.
PROVIDER_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "o3-mini",
    ],
    "google": [
        "gemini-2.0-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ],
    "bedrock": [],
    "vllm": [],
}


def get_provider_models(provider: str) -> list[str]:
    """프로바이더별 모델 목록 (기본 모델 포함, 중복 제거).

    UI 가 이 목록을 드롭다운으로 렌더. 새 provider 추가 시
    PROVIDER_MODELS 에 append → 자동 반영.
    """
    models: list[str] = []
    default = PROVIDER_DEFAULT_MODEL.get(provider.lower(), "")
    if default:
        models.append(default)
    for m in PROVIDER_MODELS.get(provider.lower(), []):
        if m and m not in models:
            models.append(m)
    return models


def register_provider(name: str, cls: Type[LLMProvider]) -> None:
    """프로바이더 등록. 기존 이름이면 덮어씀."""
    _REGISTRY[name.lower()] = cls
    logger.debug("Provider registered: %s → %s", name, cls.__name__)


def create_provider(
    name: str,
    api_key: str,
    model: str,
    base_url: Optional[str] = None,
) -> LLMProvider:
    """프로바이더 인스턴스 생성. 레지스트리에서 조회."""
    key = name.lower()

    if key not in _REGISTRY:
        _register_defaults()

    cls = _REGISTRY.get(key)
    if cls is None:
        logger.warning("Unknown provider '%s', falling back to OpenAI-compatible", name)
        cls = _REGISTRY.get("openai")
        if cls is None:
            raise ValueError(f"Provider '{name}' not registered and no OpenAI fallback")

    if base_url is None:
        base_url = os.environ.get(f"{name.upper()}_API_BASE_URL")

    return cls(api_key, model, base_url)


def get_api_key_env(provider: str) -> str:
    """프로바이더의 API 키 환경변수명 반환."""
    return PROVIDER_API_KEY_MAP.get(provider.lower(), f"{provider.upper()}_API_KEY")


def get_default_model(provider: str) -> str:
    """프로바이더의 기본 모델명 반환."""
    return PROVIDER_DEFAULT_MODEL.get(provider.lower(), "")


def list_providers() -> list[str]:
    """등록된 프로바이더 이름 목록."""
    _register_defaults()
    return list(_REGISTRY.keys())


def _register_defaults() -> None:
    """빌트인 프로바이더 등록.

    - anthropic: Anthropic Messages API (httpx SSE)
    - openai: OpenAI Chat Completions API (httpx SSE)
    - google: Gemini → OpenAI 호환 엔드포인트
    - bedrock: AWS Bedrock → OpenAI 호환 (프록시 또는 직접)
    - vllm: vLLM → OpenAI 호환 엔드포인트

    새 프로바이더 추가: register_provider("name", ProviderClass)
    """
    if _REGISTRY:
        return
    from .anthropic import AnthropicProvider
    from .openai import OpenAIProvider

    _REGISTRY["anthropic"] = AnthropicProvider
    _REGISTRY["openai"] = OpenAIProvider
    # OpenAI 호환 프로바이더 — 동일 클래스, base_url만 다름
    _REGISTRY["google"] = OpenAIProvider
    _REGISTRY["bedrock"] = OpenAIProvider
    _REGISTRY["vllm"] = OpenAIProvider


def wrap_langchain(llm, provider_name: str = "") -> LLMProvider:
    """LangChain BaseChatModel을 하네스 LLMProvider로 래핑.

    xgen에서 이미 만든 LLM 인스턴스를 하네스에 그대로 끼울 때 사용.

    Usage:
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(model="claude-sonnet-4-20250514", ...)
        provider = wrap_langchain(llm)
        state.provider = provider
    """
    from .langchain_adapter import LangChainAdapter
    return LangChainAdapter(llm, provider_name)


__all__ = [
    "LLMProvider", "ProviderEvent", "ProviderEventType",
    "register_provider", "create_provider", "wrap_langchain",
    "get_api_key_env", "get_default_model", "list_providers",
    "get_provider_models",
    "PROVIDER_API_KEY_MAP", "PROVIDER_DEFAULT_MODEL", "PROVIDER_MODELS",
]
