"""
S02 Memory — 대화 이력/기억 로드

- 이전 실행 결과를 state.previous_results에서 가져옴
- conversation_history가 있으면 messages에 추가
- embedding_search 전략: 임베딩 기반으로 관련 기억 검색
- 기억이 없으면 bypass
"""

import logging

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..core.service_registry import get_service_url

logger = logging.getLogger("harness.stage.memory")


class MemoryStage(Stage):

    @property
    def stage_id(self) -> str:
        return "s02_memory"

    @property
    def order(self) -> int:
        return 2

    def should_bypass(self, state: PipelineState) -> bool:
        strategy_name = self.get_param("strategy", state, "default")
        # embedding_search 전략은 conversation_history 없어도 실행 가능
        if strategy_name == "embedding_search":
            return not state.user_input
        return not state.previous_results and not state.conversation_history

    async def execute(self, state: PipelineState) -> dict:
        strategy_name = self.get_param("strategy", state, "default")

        if strategy_name == "embedding_search":
            return await self._execute_embedding_search(state)

        return await self._execute_default(state)

    async def _execute_default(self, state: PipelineState) -> dict:
        """기본 전략: 대화 이력 + previous_results"""
        injected = 0
        max_history = int(self.get_param("max_history", state, 10))

        # 1. 대화 이력이 있으면 messages에 추가 (max_history로 제한)
        if state.conversation_history:
            history = state.conversation_history[-max_history:]
            for msg in history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    state.messages.insert(len(state.messages) - 1, {"role": role, "content": content})
                    injected += 1

        # 2. 이전 실행 결과를 previous_results로 system prompt에 전달
        # (s03_system_prompt에서 처리 — 여기서는 state에 이미 있으므로 패스)
        prev_count = len(state.previous_results)

        logger.info("[Memory] injected=%d messages (max_history=%d), previous_results=%d", injected, max_history, prev_count)
        return {
            "injected": injected,
            "previous_results": prev_count,
            "strategy": "default",
        }

    async def _execute_embedding_search(self, state: PipelineState) -> dict:
        """임베딩 기반 관련 기억 검색.

        ServiceProvider.documents가 등록되어 있으면
        "memory" 컬렉션에서 user_input을 쿼리로 임베딩 검색.
        검색된 과거 상호작용을 시스템 프롬프트에 추가한다.
        """
        collection = self.get_param("memory_collection", state, "memory")
        top_k = int(self.get_param("memory_top_k", state, 5))
        score_threshold = float(self.get_param("memory_score_threshold", state, 0.3))

        # 기본 전략도 함께 실행 (대화 이력 주입)
        base_result = await self._execute_default(state)

        # documents 서비스가 없으면 스킵
        docs_url = get_service_url("documents")
        if not docs_url:
            logger.info("[Memory] documents service not registered, embedding_search skipped")
            base_result["strategy"] = "embedding_search"
            base_result["embedding_results"] = 0
            return base_result

        # ServiceProvider 경로 (어댑터가 등록)
        services = state.metadata.get("services")
        memories = []
        if services and hasattr(services, "documents") and services.documents:
            try:
                memories = await self._search_via_service(
                    services.documents, collection, state.user_input, top_k, score_threshold
                )
            except Exception as e:
                logger.warning("[Memory] ServiceProvider embedding search failed: %s", e)

        # ServiceProvider 실패 시 HTTP 직접 호출 폴백
        if not memories:
            memories = await self._search_via_http(
                docs_url, collection, state.user_input, state.user_id or "0",
                top_k, score_threshold,
            )

        # 검색 결과를 시스템 프롬프트에 추가
        if memories:
            memory_text = "\n\n<relevant_memories>\n"
            for i, mem in enumerate(memories):
                score = mem.get("score", 0)
                content = mem.get("chunk_text", mem.get("content", ""))
                memory_text += f"[{i + 1}] (relevance: {score:.2f}) {content}\n\n"
            memory_text += "</relevant_memories>"
            state.system_prompt = state.system_prompt + memory_text
            logger.info("[Memory] embedding_search: %d memories injected (collection=%s)", len(memories), collection)

        base_result["strategy"] = "embedding_search"
        base_result["embedding_results"] = len(memories)
        base_result["memory_collection"] = collection
        return base_result

    async def _search_via_service(self, doc_service, collection: str, query: str, top_k: int, threshold: float) -> list:
        """ServiceProvider.documents를 통한 검색"""
        results = await doc_service.search(
            collection_name=collection,
            query_text=query,
            limit=top_k,
            score_threshold=threshold,
        )
        return results if isinstance(results, list) else []

    async def _search_via_http(self, docs_url: str, collection: str, query: str, user_id: str, top_k: int, threshold: float) -> list:
        """HTTP 직접 호출 폴백"""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as client:
                resp = await client.post(
                    f"{docs_url}/api/retrieval/documents/search",
                    json={
                        "collection_name": collection,
                        "query_text": query,
                        "limit": top_k,
                        "score_threshold": threshold,
                    },
                    headers={
                        "x-user-id": str(user_id),
                        "x-user-name": "harness",
                        "x-user-admin": "true",
                        "x-user-superuser": "true",
                    },
                )
                if resp.status_code == 200:
                    return resp.json().get("results", [])
        except Exception as e:
            logger.debug("[Memory] HTTP embedding search failed: %s", e)
        return []

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "이전 실행 결과 + 대화 이력 로드", is_default=True),
            StrategyInfo("embedding_search", "임베딩 기반 관련 기억 검색"),
        ]
