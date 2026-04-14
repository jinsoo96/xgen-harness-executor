"""
ConfigClient — xgen-core persistent config API 래퍼

xgen-mcp-station의 config_client.py 패턴 참고.
API 키, 모델 설정 등을 xgen-core에서 가져온다.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("harness.config_client")

CORE_URL = os.environ.get("XGEN_CORE_URL", "http://xgen-core:8000")
INTERNAL_KEY = os.environ.get("XGEN_INTERNAL_API_KEY", "xgen-internal-key-2024")


class XgenConfigClient:
    """xgen-core Config API 클라이언트"""

    def __init__(self, base_url: str = CORE_URL):
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Content-Type": "application/json",
            "X-API-Key": INTERNAL_KEY,
        }

    async def get_value(self, env_name: str, default: str = "") -> str:
        """설정 값 가져오기 (POST /api/data/config/get-value)"""
        url = f"{self._base_url}/api/data/config/get-value"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.post(
                    url,
                    json={"env_name": env_name, "default": default},
                    headers=self._headers,
                )
                if resp.status_code == 200:
                    return resp.json().get("value", default)
        except Exception as e:
            logger.debug("[Config] get_value(%s) failed: %s", env_name, e)
        return default

    async def get_api_key(self, provider: str) -> Optional[str]:
        """프로바이더별 API 키 조회 (환경변수 우선 → xgen-core 폴백)"""
        key_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GEMINI_API_KEY",
            "bedrock": "AWS_ACCESS_KEY_ID",
            "vllm": "VLLM_API_KEY",
        }

        env_name = key_map.get(provider, f"{provider.upper()}_API_KEY")

        # 1. 환경변수
        key = os.environ.get(env_name, "")
        if key:
            return key

        # 2. xgen-core config
        key = await self.get_value(env_name)
        if key:
            return key

        # 3. 폴백 — 다른 프로바이더 키 시도
        for fallback_name in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
            if fallback_name == env_name:
                continue
            key = os.environ.get(fallback_name, "")
            if not key:
                key = await self.get_value(fallback_name)
            if key:
                logger.info("[Config] %s 없음, %s로 폴백", env_name, fallback_name)
                return key

        return None

    async def health(self) -> bool:
        """xgen-core 연결 확인"""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                resp = await client.get(f"{self._base_url}/api/data/config/health")
                return resp.status_code == 200
        except Exception:
            return False
