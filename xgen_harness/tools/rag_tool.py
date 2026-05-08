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
        state_ref: Optional[Any] = None,
        progressive: bool = True,
        snippet_size: int = 120,
    ):
        # v1.4.0 R3 — progressive PD 지원. state_ref 가 주입되고 progressive=True 면
        # 결과 본문을 state.pd_stores["rag"][rid] 에 보관, LLM 에게는 인덱스+snippet 만
        # 반환. LLM 이 fetch_pd(kind='rag', id='<rid>') 로 본문 lazy fetch. s06 의 자체
        # 검색이 progressive 모드로 동작하던 것과 isomorphic — 이제 도구 호출 경로에서도.
        self._collections = collections
        self._default_top_k = default_top_k
        self._doc_service = doc_service
        self._state = state_ref
        self._progressive = progressive
        self._snippet_size = snippet_size

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        collections_str = ", ".join(self._collections)
        # v1.5.4 — 사용자가 컬렉션 박은 의도를 LLM 이 명확히 인지하도록 강조.
        # 이전엔 "uploaded documents" 추상 표현이라 LLM 이 "주문 데이터" 같은 도메인 질의에
        # DB 도구로 추론해버리던 회귀. 이제 "사용자가 박은 자료" 임을 명시 + 우선 호출 안내.
        return (
            f"Search the user-attached document collections (RAG). The user has explicitly "
            f"attached these collections to ground the answer: [{collections_str}]. "
            f"Call this tool FIRST if the user's question could plausibly be answered by "
            f"information in these collections (e.g. domain-specific data, organization "
            f"records, internal knowledge). Returns indexed snippets — fetch full content "
            f"with fetch_pd(kind='rag', id='<id>') if needed."
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
    def read_only_hint(self) -> bool:
        return True

    @property
    def idempotent_hint(self) -> bool:
        return True  # 같은 쿼리·컬렉션이면 같은 결과 (인덱스 고정일 때)

    @property
    def open_world_hint(self) -> bool:
        return True  # xgen-documents / Qdrant 외부 호출

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
        """주입된 DocumentService.search() 로 검색.

        progressive=True + state_ref 주입 시: 본문은 pd_stores["rag"] 에 보관,
        반환은 인덱스+snippet 만. LLM 이 fetch_pd(kind='rag', id=...) 로 본문 pull.
        그렇지 않으면 [DOC_N] 포맷 문자열 (eager — 기존 v1.3.x 까지).
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

        if self._progressive and self._state is not None and hasattr(self._state, "pd_store"):
            return self._format_progressive(results, collection_name)
        return self._format_results(results)

    def _format_progressive(self, results: list, collection_name: str) -> str:
        """v1.4.0 R3 — 결과를 pd_stores 에 박고 인덱스+snippet 만 반환."""
        from ..utils.docs import extract_source, extract_text, extract_score
        lines: list[str] = []
        for i, doc in enumerate(results, 1):
            if not isinstance(doc, dict):
                continue
            text = extract_text(doc) or doc.get("chunk_text", "") or ""
            source = extract_source(doc) or doc.get("file_name", "") or ""
            score = extract_score(doc)
            rid = f"{collection_name}#{i}"
            snippet = (text[: self._snippet_size] + "…") if len(text) > self._snippet_size else text
            snippet = snippet.replace("\n", " ")
            score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "-"
            lines.append(f"[{i}] id={rid} · {source} ({score_str}) · {snippet}")
            try:
                self._state.pd_store(
                    kind="rag",
                    resource_id=rid,
                    preview=snippet,
                    full=text,
                    meta={
                        "collection": collection_name,
                        "index": i,
                        "source": source,
                        "score": score,
                        "chars": len(text),
                    },
                )
            except Exception as e:
                logger.debug("[RAG Tool] pd_store failed for %s: %s", rid, e)
        if not lines:
            return ""
        lines.append("")
        lines.append(
            "(본문이 필요하면 fetch_pd(kind='rag', id='<위 id>') 호출. "
            f"예: fetch_pd(kind='rag', id='{collection_name}#1'))"
        )
        return "\n".join(lines)

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
