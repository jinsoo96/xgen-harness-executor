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
from ..core.service_registry import get_service_url

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

    async def get_schema_summary(
        self, connection_name: str, max_tables: int = 20
    ) -> str:
        """db_manager 가 제공하는 스키마/테이블 조회 메서드를 자동 발견하여 위임.

        SQL/dialect 를 라이브러리에서 박지 않음. db_manager 구현체(PostgreSQL/MySQL/Oracle/...)
        가 가진 introspection 메서드를 다중 후보로 순차 시도 → 첫 성공값 사용.
        없으면 빈 문자열 (graceful skip).
        """
        if not connection_name:
            return ""

        # 1순위: 구현체가 자체 요약 메서드를 가진 경우 (가장 dialect-aware)
        for name in ("get_schema_summary", "describe_schema", "describe_connection"):
            fn = getattr(self._db, name, None)
            if callable(fn):
                try:
                    result = fn(connection_name, max_tables)
                    if hasattr(result, "__await__"):
                        result = await result
                    if isinstance(result, str) and result:
                        return result
                except Exception as e:
                    logger.warning("[DB] %s(%s) failed: %s", name, connection_name, e)

        # 2순위: 테이블 목록만 반환하는 메서드 (dialect-agnostic)
        for name in ("list_tables", "get_tables", "tables_in_schema"):
            fn = getattr(self._db, name, None)
            if callable(fn):
                try:
                    result = fn(connection_name)
                    if hasattr(result, "__await__"):
                        result = await result
                    if isinstance(result, list) and result:
                        tables = [str(t) for t in result[:max_tables]]
                        return f"[DB:{connection_name}] tables ({len(tables)}): {', '.join(tables)}"
                except Exception as e:
                    logger.warning("[DB] %s(%s) failed: %s", name, connection_name, e)

        # 3순위: SQLAlchemy inspector 호환 (engine/inspect 속성)
        for attr in ("inspect", "inspector", "engine"):
            obj = getattr(self._db, attr, None)
            if obj is None:
                continue
            try:
                # SQLAlchemy: inspect(engine).get_table_names(schema=...)
                from sqlalchemy import inspect as sa_inspect
                inspector = sa_inspect(obj) if attr == "engine" else obj
                tables = inspector.get_table_names(schema=connection_name)[:max_tables]
                if tables:
                    return f"[DB:{connection_name}] tables ({len(tables)}): {', '.join(tables)}"
            except Exception:
                continue

        logger.info(
            "[DB] get_schema_summary(%s): db_manager 에서 introspection 메서드 미발견 (db_manager=%s)",
            connection_name, type(self._db).__name__,
        )
        return ""

    async def execute_raw_query(
        self, query: str, params: Optional[list] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """raw SQL 실행 — DB 도구 노드용.

        db_manager 가 execute_query / query / fetchall 계열을 갖는 경우 자동 사용.
        """
        candidates = ("execute_query", "query", "fetch_all", "fetchall")
        for name in candidates:
            fn = getattr(self._db, name, None)
            if not callable(fn):
                continue
            try:
                result = fn(query, params or [])
                if hasattr(result, "__await__"):
                    result = await result
                if isinstance(result, list):
                    return result[:limit]
                if isinstance(result, dict):
                    return [result]
            except Exception as e:
                logger.warning("[DB] execute_raw_query via %s failed: %s", name, e)
                break
        logger.warning("[DB] raw query 실행 경로를 찾지 못함 (db_manager=%s)", type(self._db).__name__)
        return []


# ──────────────────────────────────────────────
# 2. XgenConfigService
# ──────────────────────────────────────────────

class XgenConfigService:
    """xgen-core Config API 래핑.

    POST /api/data/config/get-value로 설정 조회.
    Redis(xgen-core Config) → 환경변수 → 폴백 순서로 API 키 해석.
    Redis 우선 순서: 관리자가 UI에서 런타임 변경한 값을 반영하기 위함.
    """

    # providers/__init__.py의 단일 진실 소스 참조
    from ..providers import PROVIDER_API_KEY_MAP
    _PROVIDER_KEY_MAP = PROVIDER_API_KEY_MAP

    def __init__(self, base_url: str, internal_key: str = "", event_emitter=None):
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Content-Type": "application/json",
            "X-API-Key": internal_key or os.environ.get(
                "XGEN_INTERNAL_API_KEY", "xgen-internal-key-2024"
            ),
        }
        # verbose 모드 ServiceLookupEvent 발행용 (어댑터가 EventEmitter 주입)
        self._event_emitter = event_emitter

    async def _emit_lookup(self, key: str, source: str, hit: bool, provider: str = "") -> None:
        """verbose 모드에서 조회 경로 이벤트 발행."""
        if self._event_emitter is None:
            return
        try:
            from ..events.types import ServiceLookupEvent
            await self._event_emitter.emit(ServiceLookupEvent(
                key=key, source=source, hit=hit, provider=provider,
            ))
        except Exception:
            pass  # 이벤트 발행은 조회 실패에 영향 주지 않음

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

        # 1. Redis 기반 xgen-core config (관리자 UI 런타임 변경 반영)
        key = await self.get_value(key_name)
        if key:
            await self._emit_lookup(key_name, "redis", True, provider)
            return key

        # 2. 환경변수 (.env) 폴백
        key = os.environ.get(key_name, "")
        if key:
            await self._emit_lookup(key_name, "env", True, provider)
            return key

        # 3. 다른 프로바이더 폴백
        for fallback in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
            if fallback == key_name:
                continue
            key = os.environ.get(fallback, "") or await self.get_value(fallback)
            if key:
                logger.info("[Config] %s 없음, %s로 폴백", key_name, fallback)
                await self._emit_lookup(fallback, "fallback", True, provider)
                return key

        await self._emit_lookup(key_name, "missing", False, provider)
        return None

    async def get_setting(
        self, key: str, *, default: Optional[str] = None
    ) -> Optional[str]:
        """일반 설정 조회 — Redis → .env → default 순서 강제.

        `get_value` 가 이미 Redis(xgen-core) 조회를 담당하고, 실패 시 default 반환.
        여기선 Redis 미스일 때만 .env 를 추가로 시도하고, source 를 이벤트로 발행.
        """
        # 1. Redis
        value = await self.get_value(key, default="")
        if value:
            await self._emit_lookup(key, "redis", True)
            return value
        # 2. .env
        value = os.environ.get(key, "")
        if value:
            await self._emit_lookup(key, "env", True)
            return value
        # 3. default
        await self._emit_lookup(key, "missing", False)
        return default


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

    @staticmethod
    def _auth_headers(user_id: str = "") -> dict:
        """ExecutionContext에서 인증 헤더 구성. user_id 명시 시 우선 사용."""
        try:
            from ..core.execution_context import get_extra
            ctx_uid = get_extra("user_id", "") or ""
            ctx_admin = str(get_extra("user_is_admin", "true"))
            ctx_super = str(get_extra("user_is_superuser", "true"))
        except Exception:
            ctx_uid, ctx_admin, ctx_super = "", "true", "true"
        uid = user_id or ctx_uid
        return {
            "x-user-id": str(uid),
            "x-user-name": "harness",
            "x-user-admin": ctx_admin,
            "x-user-superuser": ctx_super,
        }

    async def search(
        self,
        query: str,
        collection_id: str,
        limit: int = 5,
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        """xgen-documents: POST /api/retrieval/documents/search (collection_name + query_text)"""
        url = f"{self._base_url}/api/retrieval/documents/search"
        payload = {
            "query_text": query,
            "collection_name": collection_id,
            "limit": limit,
            "score_threshold": 0.0,
        }
        headers = self._auth_headers(user_id)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("results", data.get("documents", data if isinstance(data, list) else []))
                logger.warning("[Documents] search %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("[Documents] search failed: %s", e)
        return []

    async def list_collections(self, user_id: str = "") -> list[dict[str, Any]]:
        url = f"{self._base_url}/api/retrieval/collections"
        params = {"user_id": user_id} if user_id else {}
        headers = self._auth_headers(user_id)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("collections", data if isinstance(data, list) else [])
                logger.warning("[Documents] list_collections %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("[Documents] list_collections failed: %s", e)
        return []

    async def embed_query(self, text: str, user_id: str = "") -> list[float]:
        """xgen-documents: /api/embedding/query-embedding"""
        url = f"{self._base_url}/api/embedding/query-embedding"
        headers = self._auth_headers(user_id)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json={"text": text}, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("embedding", data.get("vector", []))
                logger.warning("[Documents] embed_query %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("[Documents] embed_query failed: %s", e)
        return []

    async def rerank(
        self, query: str, documents: list[str], top_k: int = 5, user_id: str = "",
    ) -> list[dict[str, Any]]:
        """xgen-documents 리랭커 — /api/embedding/reranker/rerank (없으면 빈 결과)."""
        url = f"{self._base_url}/api/embedding/reranker/rerank"
        headers = self._auth_headers(user_id)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    url,
                    json={"query": query, "documents": documents, "top_k": top_k},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("results", data if isinstance(data, list) else [])
                logger.debug("[Documents] rerank %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.debug("[Documents] rerank failed: %s", e)
        return []

    async def list_folders(self, user_id: str = "") -> list[dict[str, Any]]:
        """xgen-documents: /api/folder/list"""
        url = f"{self._base_url}/api/folder/list"
        headers = self._auth_headers(user_id)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("folders", data if isinstance(data, list) else [])
        except Exception as e:
            logger.debug("[Documents] list_folders failed: %s", e)
        return []

    async def ontology_query(
        self, collection_id: str, query: str, user_id: str = "",
    ) -> dict[str, Any]:
        """xgen-documents GraphRAG — /api/ontology/graph-rag/multi-turn (있을 때만)."""
        url = f"{self._base_url}/api/ontology/graph-rag/multi-turn"
        headers = self._auth_headers(user_id)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    url,
                    json={"collection_id": collection_id, "query": query},
                    headers=headers,
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug("[Documents] ontology_query failed: %s", e)
        return {}


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
        _core_url = core_url or get_service_url("config")
        config_svc = XgenConfigService(_core_url) if _core_url else None

        # MCP
        _mcp_url = mcp_url or get_service_url("mcp")
        mcp_svc = XgenMCPService(_mcp_url) if _mcp_url else None

        # Documents
        _docs_url = documents_url or get_service_url("documents")
        docs_svc = XgenDocumentService(_docs_url) if _docs_url else None

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
