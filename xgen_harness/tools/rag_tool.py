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


# v1.7.4 — PD 패턴 (지도→인덱스→원문) 유지하되, "지도" 단계 정보량 풍부화.
# v1.7.3 (default OFF) 은 PD 자체를 폐기하는 잘못된 진단이라 v1.7.4 에서 즉시 환원.
# 사용자 의도: 가용한 도구/RAG/정책의 메타(지도) 노출 → LLM 이 인덱스 보고 원문 fetch →
#   원문 기반 답변 합성. 이 흐름이 **작동** 해야 한다.
# 분노 사례 (9 라운드 헛 fetch) 의 진짜 원인: 인덱스가 너무 빈약 (snippet 120자 + source
#   + score 만) → LLM 이 어떤 청크 fetch 할지 판단 못 함 → 같은 쿼리 반복 → max_iter 도달.
# v1.7.4 정책:
#   - default ON — 인덱스+풍부 메타 노출, 본문은 pd_stores 보관, fetch_pd 로 lazy 가져감.
#   - 인덱스에 chunk_index / total_chunks / length / source 박음 + snippet 250자
#     (이전 120 → 2x ↑). PD "지도" 의 본분 (=메타) 유지하면서 LLM 판단 정보량 보강.
#     500자 시도는 "지도" 라기엔 두꺼워 PD 와 비대칭. 250자는 claude skills 의
#     description (~150자) 보다 약간 두껍지만 청크 본문 미리보기 역할 정합.
#   - auto_threshold_chars 미사용 (= 0). 명시 인자만 override.
_PROGRESSIVE_POLICY: dict[str, Any] = {
    "enabled": True,                # default ON — PD 패턴 정상 작동
    "auto_threshold_chars": 0,      # 0 = 자동 임계 미사용. 명시 인자만 override
    "snippet_size": 250,            # v1.7.4 — 120 → 250 ("지도" 본분 유지 + 정보량 ↑)
}


def register_progressive_policy(
    *,
    enabled: Optional[bool] = None,
    auto_threshold_chars: Optional[int] = None,
    snippet_size: Optional[int] = None,
) -> None:
    """RAG 검색 결과 progressive PD 정책 외부 override.

    호스트 측에서 결과 데이터의 평균 크기 / LLM context window / 비용 목표에 맞춰 임계 조정.
    예: 호스트가 항상 작은 청크만 다루면 ``enabled=False, auto_threshold_chars=10_000_000`` —
    progressive 사실상 영구 OFF.
    """
    if enabled is not None:
        _PROGRESSIVE_POLICY["enabled"] = bool(enabled)
    if auto_threshold_chars is not None:
        _PROGRESSIVE_POLICY["auto_threshold_chars"] = int(auto_threshold_chars)
    if snippet_size is not None:
        _PROGRESSIVE_POLICY["snippet_size"] = int(snippet_size)


def get_progressive_policy() -> dict[str, Any]:
    return dict(_PROGRESSIVE_POLICY)


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
        progressive: Optional[bool] = None,
        snippet_size: Optional[int] = None,
    ):
        # v1.7.3 — progressive 가 미명시(None) 면 _PROGRESSIVE_POLICY 의 enabled +
        # auto_threshold_chars 가 결정. 명시 True/False 면 그것 우선.
        # snippet_size 도 동일 — 미명시 시 policy default.
        self._collections = collections
        self._default_top_k = default_top_k
        self._doc_service = doc_service
        self._state = state_ref
        self._progressive_explicit = progressive
        self._snippet_size_override = snippet_size

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        collections_str = ", ".join(self._collections)
        # v1.7.4 — PD 패턴 명시. 검색 = 풍부한 인덱스 (지도) 반환, 원문은 fetch_pd 로 가져감.
        # LLM 에게 "지도 → 필요한 원문 fetch → 합성 답변" 흐름을 명확히 학습시킨다.
        return (
            f"Search the user-attached document collections (RAG). The user has explicitly "
            f"attached these collections to ground the answer: [{collections_str}]. "
            f"Call this tool FIRST if the user's question could plausibly be answered by "
            f"information in these collections (e.g. domain-specific data, organization "
            f"records, internal knowledge). Returns an INDEX (map) of relevant chunks — each "
            f"entry shows source, chunk position, length, and a 250-char preview. To read "
            f"the full body of a specific chunk, call fetch_pd(kind='rag', id='<id>'). "
            f"Strategy: read the index first to decide WHICH chunks are worth fetching, "
            f"then fetch only those, then synthesize. Avoid repeating the same search query "
            f"— if the index doesn't show what you need, try a different query or report "
            f"insufficient data and stop."
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

        v1.7.3 — 기본 동작은 결과를 통째 박는 eager 포맷 ([DOC_N] tagged content).
        결과 본문 합산이 _PROGRESSIVE_POLICY['auto_threshold_chars'] 를 초과하거나
        생성자에서 progressive=True 명시한 경우에만 인덱스+snippet + pd_store 모드.
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

        use_progressive = self._should_use_progressive(results)
        if use_progressive and self._state is not None and hasattr(self._state, "pd_store"):
            return self._format_progressive(results, collection_name)
        return self._format_results(results)

    def _should_use_progressive(self, results: list) -> bool:
        """progressive 사용 여부 결정 — 명시 인자 > 자동 임계 > policy default."""
        if self._progressive_explicit is not None:
            return bool(self._progressive_explicit)
        policy = _PROGRESSIVE_POLICY
        if policy.get("enabled"):
            return True
        threshold = int(policy.get("auto_threshold_chars") or 0)
        if threshold <= 0:
            return False
        try:
            from ..utils.docs import extract_text
            total_chars = 0
            for doc in results:
                if isinstance(doc, dict):
                    total_chars += len(extract_text(doc) or doc.get("chunk_text") or "")
                elif isinstance(doc, str):
                    total_chars += len(doc)
                if total_chars > threshold:
                    return True
        except Exception:
            return False
        return False

    def _effective_snippet_size(self) -> int:
        if self._snippet_size_override is not None:
            return int(self._snippet_size_override)
        return int(_PROGRESSIVE_POLICY.get("snippet_size") or 120)

    def _format_progressive(self, results: list, collection_name: str) -> str:
        """v1.4.0 R3 — 결과를 pd_stores 에 박고 인덱스+snippet 만 반환.

        v1.7.4 — 인덱스 정보량 풍부화. snippet 250자 + chunk_index / total_chunks /
        length 메타 박음. LLM 이 진짜 "지도" 로 사용할 수 있게.
        """
        from ..utils.docs import extract_source, extract_text, extract_score
        snippet_size = self._effective_snippet_size()
        lines: list[str] = []
        for i, doc in enumerate(results, 1):
            if not isinstance(doc, dict):
                continue
            text = extract_text(doc) or doc.get("chunk_text", "") or ""
            source = extract_source(doc) or doc.get("file_name", "") or ""
            score = extract_score(doc)
            # v1.7.4 — 청킹 메타 (있는 경우만, 백엔드별 키 후보 시도)
            chunk_idx = (
                doc.get("chunk_index")
                or doc.get("chunk_idx")
                or (doc.get("metadata") or {}).get("chunk_index")
            )
            total_chunks = (
                doc.get("total_chunks")
                or (doc.get("metadata") or {}).get("total_chunks")
            )
            rid = f"{collection_name}#{i}"
            snippet = (text[: snippet_size] + "…") if len(text) > snippet_size else text
            snippet = snippet.replace("\n", " ")
            score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "-"
            # 메타 prefix — LLM 이 위치 / 크기 보고 fetch 결정
            meta_bits = [f"len={len(text)}"]
            if chunk_idx is not None:
                pos = f"chunk={chunk_idx}"
                if total_chunks:
                    pos += f"/{total_chunks}"
                meta_bits.append(pos)
            meta_str = " · ".join(meta_bits)
            lines.append(f"[{i}] id={rid} · {source} ({score_str}) · {meta_str} · {snippet}")
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
