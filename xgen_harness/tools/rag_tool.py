"""RAG Search Tool — 에이전트가 문서 검색을 직접 호출하는 도구.

pre-search(s06)와 달리, 에이전트가 필요할 때 직접 검색 질의를 작성하여 호출.
"""

import logging
from typing import Optional

from .base import Tool, ToolResult
from ..core.service_registry import get_service_url

logger = logging.getLogger("harness.tools.rag")


class RAGSearchTool(Tool):
    """에이전트가 직접 호출하는 RAG 문서 검색 도구.

    s06_context의 pre-search와 달리, 에이전트가 대화 중에 필요하다고 판단할 때
    직접 검색 질의를 작성하여 호출한다.
    """

    def __init__(
        self,
        collections: list[str],
        default_top_k: int = 4,
    ):
        self._collections = collections
        self._default_top_k = default_top_k

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        collections_str = ", ".join(self._collections)
        return (
            f"Search through document collections for relevant information. "
            f"Available collections: [{collections_str}]. "
            f"Use this when you need to find specific information from uploaded documents."
        )

    @property
    def input_schema(self) -> dict:
        schema: dict = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to find relevant documents. Be specific and descriptive.",
                },
                "collection_name": {
                    "type": "string",
                    "description": (
                        f"Document collection to search in. "
                        f"Defaults to '{self._collections[0]}' if omitted. "
                        f"Available: {', '.join(self._collections)}"
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": f"Number of results to return (default: {self._default_top_k}).",
                },
            },
            "required": ["query"],
        }
        return schema

    @property
    def category(self) -> str:
        return "retrieval"

    @property
    def is_read_only(self) -> bool:
        return True

    async def execute(self, input_data: dict) -> ToolResult:
        query = input_data.get("query", "")
        if not query:
            return ToolResult.error("query is required.")

        collection_name = input_data.get("collection_name", self._collections[0])
        top_k = input_data.get("top_k", self._default_top_k)

        # 유효한 컬렉션인지 확인
        if collection_name not in self._collections:
            return ToolResult.error(
                f"Collection '{collection_name}' not available. "
                f"Available collections: {', '.join(self._collections)}"
            )

        try:
            result_text = await self._search_documents(query, collection_name, top_k)
            if not result_text:
                return ToolResult.success(
                    f"No results found for query: '{query}' in collection '{collection_name}'."
                )
            return ToolResult.success(result_text, collection=collection_name, top_k=top_k)
        except Exception as e:
            logger.error("[RAG Tool] Search failed: %s", e)
            return ToolResult.error(f"Document search failed: {str(e)}")

    async def _search_documents(
        self, query: str, collection_name: str, top_k: int,
    ) -> str:
        """xgen-documents API를 호출하여 검색 결과를 포맷팅하여 반환."""
        import httpx

        docs_url = get_service_url("documents")
        if not docs_url:
            raise RuntimeError(
                "Documents service is not registered. "
                "RAG search is unavailable."
            )

        url = f"{docs_url}/api/retrieval/documents/search"
        # xgen-documents 스키마: collection_name (단수) + query_text + limit
        payload = {
            "query_text": query,
            "collection_name": collection_name,
            "limit": top_k,
            "score_threshold": 0.0,
        }
        # xgen-documents 인증: ExecutionContext의 user_id를 헤더로 전달
        from ..core.execution_context import get_extra
        user_id = get_extra("user_id", "") or ""
        headers = {
            "Content-Type": "application/json",
            "x-user-id": str(user_id),
            "x-user-name": "harness",
            "x-user-admin": str(get_extra("user_is_admin", "true")),
            "x-user-superuser": str(get_extra("user_is_superuser", "true")),
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Documents API returned {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            results = data.get("results", data.get("documents", []))
            if not results:
                return ""

            return self._format_results(results)

    @staticmethod
    def _format_results(results: list) -> str:
        """검색 결과를 [DOC_N] 태그 포맷으로 변환."""
        chunks: list[str] = []
        for i, doc in enumerate(results, 1):
            tag = f"[DOC_{i}]"
            if isinstance(doc, dict):
                content = doc.get("content", doc.get("text", doc.get("page_content", "")))
                source = doc.get("metadata", {}).get("source", doc.get("source", ""))
                score = doc.get("score", doc.get("similarity", None))

                header = tag
                if source:
                    header += f" (source: {source})"
                if score is not None:
                    score_str = f"{score:.3f}" if isinstance(score, float) else str(score)
                    header += f" [score: {score_str}]"

                chunks.append(f"{header}\n{content}")
            elif isinstance(doc, str):
                chunks.append(f"{tag}\n{doc}")

        return "\n\n".join(chunks)
