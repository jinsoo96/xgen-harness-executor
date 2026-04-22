"""
Provider Bootstrap — state.provider lazy 초기화 공용 헬퍼.

v0.12.0 에서 s07_llm._lazy_init_provider 로직을 여기로 이관. Planner(s00) 도
동일 경로로 provider 를 띄우고, s07 는 이 함수에 위임한다. 중복 제거 + Planner
독립성 확보.

조회 순서 (변경 없음 — feedback_redis_env_order 준수):
  1. ExecutionContext(context var)
  2. ServiceProvider.config (Redis 우선)
  3. 환경변수
  4. 파일 폴백 (XGEN_HARNESS_API_KEY_FILE_DIR)
"""

from __future__ import annotations

import logging
import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .state import PipelineState
    from ..providers.base import LLMProvider

logger = logging.getLogger("harness.provider_bootstrap")


async def ensure_provider(state: "PipelineState", *, stage_id: str = "") -> "LLMProvider":
    """state.provider 가 없으면 config 를 바탕으로 생성해 주입.

    이미 주입되어 있으면 그대로 반환. API 키/모델/base_url 해석 순서는 기존 s07
    로직 그대로 (Redis 우선). 해석 실패 시 `PipelineAbortError` raise.

    Parameters
    ----------
    state : PipelineState
    stage_id : str
        에러 이벤트 추적용 식별자. s00_harness / s07_llm 등.
    """
    if state.provider is not None:
        return state.provider

    from ..errors import PipelineAbortError
    from .execution_context import get_api_key as ctx_get_api_key
    from ..providers import (
        create_provider, get_api_key_env, resolve_api_key_from_file,
        PROVIDER_DEFAULT_MODEL,
    )

    config = state.config
    if not config:
        raise PipelineAbortError("Config not set", stage_id or "provider_bootstrap")

    provider_name: str = (config.provider or "").lower()
    model_name: str = config.model or PROVIDER_DEFAULT_MODEL.get(provider_name, "")
    if not provider_name or not model_name:
        raise PipelineAbortError(
            f"Provider/model not resolved (provider={provider_name!r}, model={model_name!r})",
            stage_id or "provider_bootstrap",
        )

    # ━━━━ API key ━━━━
    api_key: Optional[str] = ctx_get_api_key()
    if not api_key:
        services = state.metadata.get("services")
        if services and getattr(services, "config", None):
            try:
                api_key = await services.config.get_api_key(provider_name)
            except Exception as e:
                logger.debug("[provider_bootstrap] ServiceProvider API key lookup failed: %s", e)
    if not api_key:
        env_var = get_api_key_env(provider_name)
        api_key = os.environ.get(env_var, "")
        if not api_key:
            api_key = resolve_api_key_from_file(provider_name)
    if not api_key:
        raise PipelineAbortError(
            f"{provider_name} API key not configured",
            stage_id or "provider_bootstrap",
        )

    # ━━━━ base_url ━━━━
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
            logger.debug("[provider_bootstrap] base_url Redis 조회 실패: %s", e)
    if not base_url:
        base_url = os.environ.get(env_var_url, "") or None

    state.provider = create_provider(provider_name, api_key, model_name, base_url=base_url)
    logger.info(
        "[provider_bootstrap] lazy init provider=%s, model=%s (called_from=%s)",
        provider_name, model_name, stage_id or "?",
    )
    return state.provider
