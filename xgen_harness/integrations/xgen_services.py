"""
XgenServiceProvider — xgen 서비스 연동 구현체

xgen-core, xgen-documents, xgen-mcp-station, DB를
ServiceProvider 프로토콜로 래핑.

각 서비스는 연결 실패 시 None → 하네스는 해당 기능 graceful skip.

사용:
    provider = XgenServiceProvider.create(db_manager=db_manager)
    # 또는 개별 생성
    provider = XgenServiceProvider.create(
        db_manager=db_manager,
        core_url="http://xgen-core:8000",
        mcp_url="http://xgen-mcp-station:8000",
        documents_url="http://xgen-documents:8000",
    )
"""

import json
import logging
import os
from typing import Any, Optional

import httpx

from ..core.services import (
    ConfigService,
    DatabaseService,
    DocumentService,
    MCPService,
    ServiceProvider,
)

logger = logging.getLogger("harness.xgen_services")


# ──────────────────────────────────────────────
# 1. XgenDatabaseService
# ──────────────────────────────────────────────

class XgenDatabaseService:
    """xgen-workflow의 DatabaseClient 래핑.

    DatabaseClient는 동기 API이므로 async wrapper를 제공.
    db_manager가 find_records_by_condition / insert / upsert_record을 가진다고 가정.
    """

    def __init__(self, db_manager):
        self._db = db_manager

    async def insert_record(self, table: str, record: dict[str, Any]) -> Optional[int]:
        try:
            result = self._db.insert(table, record)
            return result if isinstance(result, int) else None
        except Exception as e:
            logger.error("[DB] insert %s failed: %s", table, e)
            return None

    async def find_records(
        self, table: str, conditions: dict[str, Any], limit: int = 10
    ) -> list[dict[str, Any]]:
        try:
            return self._db.find_records_by_condition(table, conditions, limit=limit)
        except Exception as e:
            logger.error("[DB] find %s failed: %s", table, e)
            return []

    async def upsert_record(
        self, table: str, match: dict[str, Any], record: dict[str, Any]
    ) -> bool:
        try:
            self._db.upsert_record(table, match, record)
            return True
        except Exception as e:
            logger.error("[DB] upsert %s failed: %s", table, e)
            return False


# ──────────────────────────────────────────────
# 2. XgenConfigService
# ──────────────────────────────────────────────

class XgenConfigService:
    """xgen-core Config API 래핑.

    POST /api/data/config/get-value로 설정 조회.
    환경변수 → xgen-core → 폴백 순서로 API 키 해석.
    """

    _PROVIDER_KEY_MAP = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GEMINI_API_KEY",
        "bedrock": "AWS_ACCESS_KEY_ID",
        "vllm": "VLLM_API_KEY",
    }

    def __init__(self, base_url: str, internal_key: str = ""):
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Content-Type": "application/json",
            "X-API-Key": internal_key or os.environ.get(
                "XGEN_INTERNAL_API_KEY", "xgen-internal-key-2024"
            ),
        }

    async def get_value(self, key: str, default: str = "") -> str:
        url = f"{self._base_url}/api/data/config/get-value"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.post(
                    url,
                    json={"env_name": key, "default": default},
                    headers=self._headers,
                )
                if resp.status_code == 200:
                    return resp.json().get("value", default)
        except Exception as e:
            logger.debug("[Config] get_value(%s) failed: %s", key, e)
        return default

    async def get_api_key(self, provider: str) -> Optional[str]:
        key_name = self._PROVIDER_KEY_MAP.get(provider, f"{provider.upper()}_API_KEY")

        # 1. 환경변수
        key = os.environ.get(key_name, "")
        if key:
            return key

        # 2. xgen-core config
        key = await self.get_value(key_name)
        if key:
            return key

        # 3. 다른 프로바이더 폴백
        for fallback in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
            if fallback == key_name:
                continue
            key = os.environ.get(fallback, "") or await self.get_value(fallback)
            if key:
                logger.info("[Config] %s 없음, %s로 폴백", key_name, fallback)
                return key

        return None


# ──────────────────────────────────────────────
# 3. XgenMCPService
# ──────────────────────────────────────────────

class XgenMCPService:
    """xgen-mcp-station HTTP API 래핑.

    세션 관리, 도구 디스커버리, 도구 실행.
    """

    def __init__(self, base_url: str, timeout: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout, connect=10.0)

    async def list_sessions(self) -> list[dict[str, Any]]:
        url = f"{self._base_url}/api/mcp/sessions"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json() if isinstance(resp.json(), list) else resp.json().get("sessions", [])
        except Exception as e:
            logger.warning("[MCP] list_sessions failed: %s", e)
        return []

    async def list_tools(self, session_id: str) -> list[dict[str, Any]]:
        url = f"{self._base_url}/api/mcp/sessions/{session_id}/tools"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("tools", data.get("data", {}).get("tools", []))
        except Exception as e:
            logger.warning("[MCP] list_tools(%s) failed: %s", session_id, e)
        return []

    async def call_tool(
        self, session_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        url = f"{self._base_url}/api/mcp/mcp-request"
        payload = {
            "session_id": session_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    result_data = data.get("data", data.get("result", {}))
                    content = result_data.get("content", [])
                    if isinstance(content, list):
                        texts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                texts.append(block.get("text", ""))
                            elif isinstance(block, str):
                                texts.append(block)
                        return "\n".join(texts) if texts else json.dumps(result_data, ensure_ascii=False)
                    return str(content)
                else:
                    return f"MCP call failed ({resp.status_code}): {resp.text[:300]}"
        except Exception as e:
            return f"MCP call error: {e}"


# ──────────────────────────────────────────────
# 4. XgenDocumentService
# ──────────────────────────────────────────────

class XgenDocumentService:
    """xgen-documents Hybrid RAG / Ontology 검색.

    s03_system_prompt, s06_context에서 문서 검색.
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout, connect=10.0)

    async def search(
        self,
        query: str,
        collection_id: str,
        limit: int = 5,
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        url = f"{self._base_url}/api/retrieval/search"
        payload = {
            "query": query,
            "collection_id": collection_id,
            "limit": limit,
        }
        if user_id:
            payload["user_id"] = user_id

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("results", data.get("documents", []))
        except Exception as e:
            logger.warning("[Documents] search failed: %s", e)
        return []

    async def list_collections(self, user_id: str = "") -> list[dict[str, Any]]:
        url = f"{self._base_url}/api/retrieval/collections"
        params = {"user_id": user_id} if user_id else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("collections", data if isinstance(data, list) else [])
        except Exception as e:
            logger.warning("[Documents] list_collections failed: %s", e)
        return []


# ──────────────────────────────────────────────
# 5. XgenServiceProvider — 팩토리
# ──────────────────────────────────────────────

class XgenServiceProvider(ServiceProvider):
    """xgen 서비스 전체를 묶는 프로바이더.

    create()로 생성하면 사용 가능한 서비스만 자동 활성화.
    연결 불가한 서비스는 None → Stage에서 graceful skip.
    """

    @classmethod
    def create(
        cls,
        db_manager=None,
        core_url: str = "",
        mcp_url: str = "",
        documents_url: str = "",
    ) -> "XgenServiceProvider":
        """사용 가능한 서비스를 자동 감지하여 생성.

        Args:
            db_manager: xgen-workflow의 DatabaseClient 인스턴스
            core_url: xgen-core URL (기본: 환경변수 XGEN_CORE_URL)
            mcp_url: xgen-mcp-station URL (기본: 환경변수 MCP_STATION_URL)
            documents_url: xgen-documents URL (기본: 환경변수 XGEN_DOCUMENTS_URL)
        """
        # DB
        database = XgenDatabaseService(db_manager) if db_manager else None

        # Config (xgen-core)
        _core_url = core_url or os.environ.get("XGEN_CORE_URL", "http://xgen-core:8000")
        config_svc = XgenConfigService(_core_url)

        # MCP
        _mcp_url = mcp_url or os.environ.get("MCP_STATION_URL", "http://xgen-mcp-station:8000")
        mcp_svc = XgenMCPService(_mcp_url)

        # Documents
        _docs_url = documents_url or os.environ.get(
            "XGEN_DOCUMENTS_URL",
            os.environ.get("DOCUMENTS_SERVICE_BASE_URL", "http://xgen-documents:8000"),
        )
        docs_svc = XgenDocumentService(_docs_url)

        provider = cls(
            database=database,
            config=config_svc,
            mcp=mcp_svc,
            documents=docs_svc,
        )

        active = provider.describe()
        logger.info(
            "[XgenServices] 활성 서비스: %s",
            ", ".join(k for k, v in active.items() if v) or "없음",
        )
        return provider
