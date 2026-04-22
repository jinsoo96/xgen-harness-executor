"""RAG Search Tool — 에이전트가 문서 검색을 직접 호출하는 도구.

pre-search(s06)와 달리, 에이전트가 필요할 때 직접 검색 질의를 작성하여 호출.

v0.11.25 — 엔진 독립성 원칙 준수:
  이 도구는 xgen-documents API 경로(`/api/retrieval/documents/search`) 를 알지 않는다.
  ServiceProvider.documents (DocumentService Protocol) 구현체를 생성자에서 주입받아
  그 `search()` 메서드만 호출한다. 외부 조직이 다른 RAG 스택을 쓸 때 DocumentService
  프로토콜을 만족하는 구현체만 넘기면 이 도구는 변경 없이 동작한다.
"""

import logging
from typing import Any, Optional

from .base import Tool, ToolResult

logger = logging.getLogger("harness.tools.rag")


class RAGSearchTool(Tool):
    """에이전트가 직접 호출하는 RAG 문서 검색 도구.

    s06_context의 pre-search와 달리, 에이전트가 대화 중에 필요하다고 판단할 때
    직접 검색 질의를 작성하여 호출한다.

    Args:
        collections: 검색 가능한 컬렉션 이름 목록 — LLM 이 이 중에서 선택.
        default_top_k: collection 당 반환 결과 수 기본값.
        doc_service: `DocumentService` 프로토콜 구현체. 미주입 시 실행 단계에서
            `ToolError` 반환 — 엔진은 호스트가 안 붙여준 서비스를 상상으로 부르지 않는다.
    """

    def __init__(
        self,
        collections: list[str],
        default_top_k: int = 4,
        doc_service: Optional[Any] = None,
    ):
        self._collections = collections
        self._default_top_k = default_top_k
        self._doc_service = doc_service

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
        """주입된 DocumentService.search() 로 검색 → [DOC_N] 포맷 문자열 반환.

        v0.11.25 — 엔진은 xgen-documents API 스키마를 직접 몰라야 한다.
        httpx 경로는 제거됐다. DocumentService 가 없으면 ToolError.
        """
        from ..errors import ToolError
        if self._doc_service is None or not hasattr(self._doc_service, "search"):
            raise ToolError(
                "DocumentService is not available. RAG search is unavailable — "
                "호스트가 ResourceRegistry 에 documents 서비스를 주입해야 합니다.",
                tool_name="rag_search",
            )

        results = await self._doc_service.search(
            query, collection_name, limit=top_k, score_threshold=0.0,
        ) or []
        if not results:
            return ""
        return self._format_results(results)

    @staticmethod
    def _format_results(results: list) -> str:
        """검색 결과를 [DOC_N] 태그 포맷으로 변환."""
        from ..utils.docs import extract_source, extract_text, extract_score
        chunks: list[str] = []
        for i, doc in enumerate(results, 1):
            tag = f"[DOC_{i}]"
            if isinstance(doc, dict):
                content = extract_text(doc)
                source = extract_source(doc)
                score = extract_score(doc) or None

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
