"""
S06 Context — 컨텍스트 수집 + 윈도우 관리

역할:
1. stage_params에서 선택된 RAG 컬렉션으로 문서 검색 (xgen-documents API)
2. stage_params에서 선택된 DB 연결로 스키마/데이터 조회 (TODO)
3. 검색 결과를 system_prompt에 추가
4. 토큰 예산 초과 시 컨텍스트 압축

실행기가 하드코딩하지 않음 — stage_params에서 리소스를 읽어서 동적 실행.
"""

import logging

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..core.service_registry import get_service_url

logger = logging.getLogger("harness.stage.context")

CHARS_PER_TOKEN = 3  # 평균 추정


class ContextStage(Stage):

    @property
    def stage_id(self) -> str:
        return "s06_context"

    @property
    def order(self) -> int:
        return 6

    def should_bypass(self, state: PipelineState) -> bool:
        # 첫 번째 루프에서만 RAG 검색 실행 (이후는 토큰 관리만)
        return state.loop_iteration > 1

    async def execute(self, state: PipelineState) -> dict:
        config = state.config
        results = {"rag_chunks": 0, "rag_collections": 0, "db_results": 0, "compacted": False}

        # ── 1. RAG 검색 — stage_params에서 선택된 컬렉션 ──
        rag_collections: list[str] = self.get_param("rag_collections", state, [])
        # harness_config에서도 읽기 (프론트 ConfigPanel 호환)
        if not rag_collections and hasattr(config, 'rag_collections'):
            rag_collections = getattr(config, 'rag_collections', []) or []

        if rag_collections and state.user_input:
            rag_context = await self._fetch_rag(
                collections=rag_collections,
                query=state.user_input,
                user_id=state.user_id or "0",
                top_k=int(self.get_param("rag_top_k", state, 4)),
            )
            if rag_context:
                # system_prompt에 RAG 컨텍스트 추가
                state.system_prompt = f"{state.system_prompt}\n\n{rag_context}"
                results["rag_chunks"] = rag_context.count("[")  # 대략적 청크 수
                results["rag_collections"] = len(rag_collections)
                logger.info("[Context] RAG: %d collections, added to system prompt", len(rag_collections))

        # ── 2. DB 연결 조회 (TODO: stage_params.db_connections) ──
        db_connections: list[str] = self.get_param("db_connections", state, [])
        if db_connections:
            # TODO: DB 스키마/데이터를 가져와서 컨텍스트에 추가
            logger.info("[Context] db_connections: %d selected (not yet implemented)", len(db_connections))
            results["db_results"] = len(db_connections)

        # ── 3. 토큰 예산 관리 ──
        context_window = self.get_param("context_window", state, config.context_window if config else 200_000)
        max_tokens = config.max_tokens if config else 8192
        available_tokens = context_window - max_tokens

        if config and config.thinking_enabled:
            available_tokens -= config.thinking_budget_tokens

        total_chars = len(state.system_prompt)
        for msg in state.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(str(block.get("text", "")))
                        total_chars += len(str(block.get("content", "")))

        estimated_tokens = total_chars // CHARS_PER_TOKEN
        budget_used = estimated_tokens / available_tokens if available_tokens > 0 else 1.0

        # 압축 필요 시
        compaction_threshold = self.get_param("compaction_threshold", state, 80) / 100.0
        if budget_used > compaction_threshold and len(state.messages) > 4:
            state.messages = [state.messages[0]] + state.messages[-3:]
            results["compacted"] = True
            logger.info("[Context] Compacted: kept first + last 3 messages")

        results["estimated_tokens"] = estimated_tokens
        results["budget_used"] = round(budget_used, 2)

        logger.info("[Context] tokens=%d, budget=%.0f%%, rag=%d cols",
                    estimated_tokens, budget_used * 100, len(rag_collections))
        return results

    async def _fetch_rag(self, collections: list[str], query: str, user_id: str, top_k: int = 4) -> str:
        """xgen-documents API로 RAG 검색.

        실행기가 직접 하드코딩하지 않고, stage_params에서 선택된 컬렉션만 검색.
        """
        import httpx

        parts = []
        docs_url = get_service_url('documents')
        if not docs_url:
            logger.info("documents service not registered, skipping RAG")
            return []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                for col_name in collections:
                    try:
                        resp = await client.post(
                            f"{docs_url}/api/retrieval/documents/search",
                            json={
                                "collection_name": col_name,
                                "query_text": query,
                                "limit": top_k,
                                "score_threshold": 0.1,
                            },
                            headers={
                                "x-user-id": str(user_id),
                                "x-user-name": "harness",
                                "x-user-admin": "true",
                                "x-user-superuser": "true",
                            },
                        )
                        if resp.status_code == 200:
                            results = resp.json().get("results", [])
                            if results:
                                part = f"## {col_name} ({len(results)}건)\n\n"
                                for i, r in enumerate(results):
                                    part += f"[{i+1}] {r.get('file_name', '')} ({r.get('score', 0):.3f})\n{r.get('chunk_text', '')}\n\n"
                                parts.append(part)
                    except Exception as e:
                        logger.warning("[Context] RAG search failed for %s: %s", col_name, e)
                        continue
        except Exception as e:
            logger.error("[Context] RAG client error: %s", e)

        return "\n\n".join(parts)

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("token_budget", "RAG 검색 + 토큰 예산 압축", is_default=True),
            StrategyInfo("sliding_window", "슬라이딩 윈도우 (최근 N개 메시지)"),
        ]
