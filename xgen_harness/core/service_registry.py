"""
ServiceRegistry -- 외부 서비스 엔드포인트 중앙 관리

하드코딩된 URL을 제거하고, 환경변수 또는 명시적 등록으로 서비스 엔드포인트를 관리.
새 서비스 추가 시 코드 수정 없이 register_service()로 등록.
"""

import os
from typing import Optional

_SERVICES: dict[str, str] = {}

# Default service URLs (overridable via env vars or register_service)
_DEFAULTS = {
    "xgen-core": ("XGEN_CORE_URL", "http://xgen-core:8000"),
    "xgen-documents": ("XGEN_DOCUMENTS_URL", "http://xgen-documents:8000"),
    "xgen-mcp-station": ("MCP_STATION_URL", "http://xgen-mcp-station:8000"),
}

# Legacy env var aliases (checked as fallback)
_LEGACY_ENV_ALIASES = {
    "xgen-documents": ["DOCUMENTS_SERVICE_BASE_URL"],
    "xgen-mcp-station": ["MCP_STATION_RAW_URL"],
}


def register_service(name: str, url: str) -> None:
    """Register or override a service endpoint URL."""
    _SERVICES[name] = url.rstrip("/")


def get_service_url(name: str) -> str:
    """Get service URL. Priority: explicit registration > env var > legacy env > default."""
    # 1. Explicit registration
    if name in _SERVICES:
        return _SERVICES[name]

    # 2. Primary env var + default
    if name in _DEFAULTS:
        env_var, default = _DEFAULTS[name]
        value = os.environ.get(env_var)
        if value:
            return value

        # 3. Legacy env var aliases
        for alias in _LEGACY_ENV_ALIASES.get(name, []):
            value = os.environ.get(alias)
            if value:
                return value

        return default

    # 4. Unknown service: guess from name
    env_guess = f"{name.upper().replace('-', '_')}_URL"
    return os.environ.get(env_guess, f"http://{name}:8000")


def list_services() -> dict[str, str]:
    """List all known service endpoints."""
    result = {}
    for name in set(list(_DEFAULTS.keys()) + list(_SERVICES.keys())):
        result[name] = get_service_url(name)
    return result
