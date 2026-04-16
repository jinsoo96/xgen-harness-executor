"""
ExecutionContext — 실행별 격리된 컨텍스트

os.environ 대신 contextvars를 사용하여 동시 실행 시 API 키/설정이 섞이지 않도록 격리.
"""
import contextvars
from typing import Any, Optional

# Per-execution context variables
_api_key_var: contextvars.ContextVar[str] = contextvars.ContextVar("harness_api_key", default="")
_provider_var: contextvars.ContextVar[str] = contextvars.ContextVar("harness_provider", default="")
_model_var: contextvars.ContextVar[str] = contextvars.ContextVar("harness_model", default="")
_extra_vars: contextvars.ContextVar[dict] = contextvars.ContextVar("harness_extra")


def set_execution_context(api_key: str, provider: str = "", model: str = "", **extra: Any) -> None:
    """현재 실행 컨텍스트에 API 키/프로바이더/모델 설정.

    contextvars 기반이므로 asyncio.create_task() 각각에 자동 격리됨.
    """
    _api_key_var.set(api_key)
    if provider:
        _provider_var.set(provider)
    if model:
        _model_var.set(model)
    if extra:
        _extra_vars.set(extra)


def get_api_key() -> str:
    """현재 컨텍스트의 API 키 반환. 없으면 빈 문자열."""
    return _api_key_var.get("")


def get_provider() -> str:
    """현재 컨텍스트의 프로바이더 이름 반환."""
    return _provider_var.get("")


def get_model() -> str:
    """현재 컨텍스트의 모델 이름 반환."""
    return _model_var.get("")


def get_extra(key: str, default: Any = None) -> Any:
    """현재 컨텍스트의 추가 설정값 반환."""
    try:
        extras = _extra_vars.get()
        return extras.get(key, default)
    except LookupError:
        return default


def clear_execution_context() -> None:
    """현재 컨텍스트 초기화. 테스트 등에서 사용."""
    for var in (_api_key_var, _provider_var, _model_var, _extra_vars):
        try:
            var.set(var.default if hasattr(var, 'default') else "")  # type: ignore
        except (AttributeError, TypeError):
            pass
    _api_key_var.set("")
    _provider_var.set("")
    _model_var.set("")
