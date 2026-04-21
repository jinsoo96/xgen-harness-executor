"""
S06 Context — 컨텍스트 수집 + 윈도우 관리

역할:
1. stage_params에서 선택된 RAG 컬렉션으로 문서 검색 (xgen-documents API)
2. stage_params에서 선택된 DB 연결의 스키마 요약 조회 (ServiceProvider.database 위임)
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
        results = {
            "rag_chunks": 0, "rag_collections": 0, "db_results": 0, "compacted": False,
            "ontology_results": 0, "folders_expanded": 0, "reranked": False,
        }

        # ── 1. RAG 컬렉션 + folders 확장 ──
        rag_collections: list[str] = list(self.get_param("rag_collections", state, []) or [])
        if not rag_collections and hasattr(config, 'rag_collections'):
            rag_collections = list(getattr(config, 'rag_collections', []) or [])

        # folders → 해당 폴더 안의 컬렉션 자동 추가 (DocumentService.list_collections 위임)
        folders: list[str] = list(self.get_param("folders", state, []) or [])
        if folders:
            services = state.metadata.get("services")
            doc_service = getattr(services, "documents", None) if services else None
            if doc_service and hasattr(doc_service, "list_collections"):
                try:
                    all_cols = await doc_service.list_collections() or []
                    expanded = [
                        c.get("collection_name") for c in all_cols
                        if isinstance(c, dict)
                        and (c.get("folder_id") in folders or str(c.get("folder_id")) in folders)
                        and c.get("collection_name")
                    ]
                    for col in expanded:
                        if col not in rag_collections:
                            rag_collections.append(col)
                    results["folders_expanded"] = len(expanded)
                except Exception as e:
                    logger.warning("[Context] folder expansion failed: %s", e)

        if rag_collections and state.user_input:
            # verbose: RAG fetch 시작
            from ..events.types import StageSubstepEvent as _Sub
            top_k = int(self.get_param("rag_top_k", state, 4))
            await state.emit_verbose(_Sub(
                stage_id=self.stage_id, substep="rag_fetch_start",
                meta={"collections": rag_collections, "top_k": top_k},
            ))
            # **우선**: ServiceProvider.documents 위임 (외부 회사가 자기 구현 주입 가능).
            # **폴백**: ServiceProvider 없을 때만 직접 httpx → xgen-documents 스키마.
            services = state.metadata.get("services")
            doc_service = getattr(services, "documents", None) if services else None
            rag_context = ""
            if doc_service and hasattr(doc_service, "search"):
                parts: list[str] = []
                from ..utils.docs import extract_source, extract_text, extract_score
                score_threshold = float(self.get_param("score_threshold", state, 0.0))
                # metadata_filter — xgen-documents DocumentSearchRequest.filter 로 전달.
                # 예: {"file_name": "products.csv"} 를 주면 해당 파일 청크만 반환.
                # dict 또는 JSON 문자열(UI textarea) 모두 허용, 파싱 실패 시 무시.
                raw_filter = self.get_param("metadata_filter", state, None)
                metadata_filter: dict | None = None
                if isinstance(raw_filter, dict) and raw_filter:
                    metadata_filter = raw_filter
                elif isinstance(raw_filter, str) and raw_filter.strip():
                    try:
                        import json as _json
                        parsed = _json.loads(raw_filter)
                        if isinstance(parsed, dict) and parsed:
                            metadata_filter = parsed
                    except Exception as e:
                        logger.debug("[Context] metadata_filter JSON 파싱 실패: %s", e)
                # 서버 단 rerank — 요청 단위로 활성 가능 (xgen-documents 지원).
                # reranker 파라미터가 truthy 이면 서버 rerank 를 켜고, rerank_top_k 도 전달.
                reranker_enabled = bool(str(self.get_param("reranker", state, "") or "").strip())
                rerank_top_k_param = self.get_param("rerank_top_k", state, None)
                try:
                    rerank_top_k_int = int(rerank_top_k_param) if rerank_top_k_param is not None else None
                except (TypeError, ValueError):
                    rerank_top_k_int = None
                # RAG Progressive Disclosure 모드 — Claude Code 5-Level Compression 의 정신을
                # RAG 단에 적용. eager (기존) 는 청크 본문을 system_prompt 에 통으로 넣고,
                # progressive 는 인덱스만 넣고 본문은 pd_stores 에 보관. LLM 은
                # fetch_pd(kind='rag', id='<col>#<i>') 로 필요한 청크만 pull.
                rag_pd_mode = str(self.get_param("rag_pd_mode", state, "eager") or "eager").strip().lower()
                if rag_pd_mode not in ("eager", "progressive"):
                    rag_pd_mode = "eager"
                rag_pd_snippet = int(self.get_param("rag_pd_snippet_size", state, 120))
                for col in rag_collections:
                    try:
                        # 변수명 search_hits — 상단 results dict 와 혼동 방지 (v0.8.35 이전 regression fix)
                        search_hits = await doc_service.search(
                            state.user_input, col, limit=top_k, score_threshold=score_threshold,
                            filter=metadata_filter, rerank=reranker_enabled, rerank_top_k=rerank_top_k_int,
                        ) or []
                        if search_hits:
                            part = f"## {col} ({len(search_hits)}건)\n\n"
                            for i, r in enumerate(search_hits):
                                if not isinstance(r, dict):
                                    continue
                                src = extract_source(r) or r.get("file_name", "")
                                score = extract_score(r)
                                text = extract_text(r) or r.get("chunk_text", "")
                                if rag_pd_mode == "progressive":
                                    # 인덱스 한 줄만. 본문은 pd_stores["rag"] 에 보관.
                                    rid = f"{col}#{i+1}"
                                    snippet = (text[:rag_pd_snippet] + "…") if len(text) > rag_pd_snippet else text
                                    snippet = snippet.replace("\n", " ")
                                    part += (
                                        f"[{i+1}] id={rid} · {src} ({score:.3f}) · {snippet}\n"
                                    )
                                    state.pd_store(
                                        kind="rag",
                                        resource_id=rid,
                                        preview=snippet,
                                        full=text,
                                        meta={
                                            "collection": col,
                                            "index": i + 1,
                                            "source": src,
                                            "score": score,
                                            "chars": len(text),
                                        },
                                    )
                                else:
                                    part += f"[{i+1}] {src} ({score:.3f})\n{text}\n\n"
                            if rag_pd_mode == "progressive":
                                part += (
                                    "\n(본문이 필요하면 fetch_pd(kind='rag', id='<위 id>') 호출. "
                                    "예: fetch_pd(kind='rag', id='" + f"{col}#1" + "'))\n"
                                )
                            parts.append(part)
                    except Exception as e:
                        logger.warning("[Context] DocumentService.search failed for %s: %s", col, e)
                rag_context = "\n\n".join(parts)
            else:
                # 폴백 — ServiceProvider 미주입 환경에서 라이브러리만 단독 사용 시
                rag_context = await self._fetch_rag(
                    collections=rag_collections,
                    query=state.user_input,
                    user_id=state.user_id or "0",
                    top_k=top_k,
                    score_threshold=float(self.get_param("score_threshold", state, 0.2)),
                    use_model_prompt=bool(self.get_param("use_model_prompt", state, True)),
                )
            # rerank — DocumentService.rerank 위임.
            # Protocol: rerank(query, documents: list[str], top_k, user_id) -> [{"index", "score"}, ...]
            # xgen-documents 의 reranker provider 는 서버 기동 시 설정되므로, 본 Stage 는
            # reranker 파라미터를 "rerank 활성 토글" 로만 사용합니다 (truthy 면 호출).
            reranker_enabled: str = str(self.get_param("reranker", state, "") or "").strip()
            if reranker_enabled and rag_context:
                services = state.metadata.get("services")
                doc_service = getattr(services, "documents", None) if services else None
                if doc_service and hasattr(doc_service, "rerank"):
                    try:
                        import re as _re
                        # 2 줄 이상 공백으로 분리된 블록을 청크로 간주. 쿼리 시점에 조립된
                        # `## collection (N건)` 헤더 블록 구조를 그대로 유지합니다.
                        chunks = [c.strip() for c in _re.split(r"\n{2,}", rag_context) if c.strip()]
                        if chunks:
                            rerank_top_k = int(self.get_param("rerank_top_k", state, top_k))
                            ranked = await doc_service.rerank(
                                query=state.user_input,
                                documents=chunks,
                                top_k=rerank_top_k,
                            ) or []
                            if ranked:
                                # ranked: [{"index": i, "score": s}, ...] — score 내림차순 가정
                                ordered_chunks: list[str] = []
                                seen_idx: set[int] = set()
                                for item in ranked[:rerank_top_k]:
                                    idx = item.get("index") if isinstance(item, dict) else None
                                    if isinstance(idx, int) and 0 <= idx < len(chunks) and idx not in seen_idx:
                                        ordered_chunks.append(chunks[idx])
                                        seen_idx.add(idx)
                                if ordered_chunks:
                                    rag_context = "\n\n".join(ordered_chunks)
                                    results["reranked"] = True
                                    results["rerank_top_k"] = rerank_top_k
                    except Exception as e:
                        logger.warning("[Context] rerank failed: %s", e)
            if rag_context:
                # system_prompt에 RAG 컨텍스트 추가
                state.system_prompt = f"{state.system_prompt}\n\n{rag_context}"
                # enhance_prompt — RAG 컨텍스트 뒤에 사용자 지정 "응답 향상" 지시를 붙입니다.
                # 레거시 document_loaders.enhance_prompt 에 대응. 빈 문자열이면 생략.
                enhance_prompt = str(self.get_param("enhance_prompt", state, "") or "").strip()
                if enhance_prompt:
                    state.system_prompt = (
                        f"{state.system_prompt}\n\n"
                        f"<enhance_prompt>\n{enhance_prompt}\n</enhance_prompt>"
                    )
                    results["enhance_prompt_applied"] = True
                results["rag_chunks"] = rag_context.count("[")  # 대략적 청크 수
                results["rag_collections"] = len(rag_collections)
                logger.info("[Context] RAG: %d collections, added to system prompt", len(rag_collections))
            from ..events.types import StageSubstepEvent as _Sub2
            await state.emit_verbose(_Sub2(
                stage_id=self.stage_id, substep="rag_fetch_complete",
                meta={"chunks": results.get("rag_chunks", 0)},
            ))

        # ── 1.5 Ontology / GraphRAG — DocumentService.ontology_query 위임 ──
        ontology_collections: list[str] = list(self.get_param("ontology_collections", state, []) or [])
        if ontology_collections and state.user_input:
            services = state.metadata.get("services")
            doc_service = getattr(services, "documents", None) if services else None
            if doc_service and hasattr(doc_service, "ontology_query"):
                try:
                    onto_chunks: list[str] = []
                    for col in ontology_collections:
                        try:
                            r = await doc_service.ontology_query(state.user_input, col)
                            if r:
                                onto_chunks.append(f"[graph:{col}]\n{r if isinstance(r, str) else str(r)[:2000]}")
                        except Exception as e:
                            logger.warning("[Context] ontology_query (%s) failed: %s", col, e)
                    if onto_chunks:
                        state.system_prompt = (state.system_prompt or "") + (
                            "\n\n<graph_rag>\n" + "\n\n".join(onto_chunks) + "\n</graph_rag>"
                        )
                        results["ontology_results"] = len(onto_chunks)
                        logger.info("[Context] ontology: %d collections injected", len(onto_chunks))
                except Exception as e:
                    logger.warning("[Context] ontology pipeline error: %s", e)

        # ── 2. DB 연결 — services.database.get_schema_summary 로 위임 ──
        # 라이브러리는 connection_name 같은 추상 식별자만 다루고,
        # 실제 SQL/엔진별 해석은 ServiceProvider.database 구현체 책임.
        db_connections: list[str] = self.get_param("db_connections", state, [])
        if db_connections:
            services = state.metadata.get("services")
            if services and getattr(services, "database", None):
                summaries: list[str] = []
                for conn in db_connections:
                    try:
                        summary = await services.database.get_schema_summary(conn)
                        if summary:
                            summaries.append(summary)
                    except Exception as e:
                        logger.warning("[Context] DB schema 조회 실패 (%s): %s", conn, e)
                if summaries:
                    state.system_prompt += "\n\n<db_context>\n" + "\n".join(summaries) + "\n</db_context>"
                    results["db_results"] = len(summaries)
                    logger.info("[Context] DB context injected: %d connections", len(summaries))
                else:
                    results["db_results"] = 0
                    logger.info("[Context] db_connections: %d selected, schema 조회 결과 없음", len(db_connections))
            else:
                results["db_results"] = 0
                logger.info(
                    "[Context] db_connections: %d selected, but ServiceProvider.database 가 주입되지 않음",
                    len(db_connections),
                )

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

        # 압축 전략 디스패치 — 3 가지
        #   token_budget (기본):  first + last_3 유지. 파괴적 (원본 소실).
        #   sliding_window:        최근 N개 유지. 파괴적.
        #   context_collapse_overlay (L4, Claude Code 패턴): 비파괴 — 중간 메시지를
        #       pd_stores["history"] 에 보관 + overlay 요약 마커로 교체. fetch_pd 로 복원.
        strategy_name = self.get_param("strategy", state, "token_budget")
        compaction_threshold = self.get_param("compaction_threshold", state, 80) / 100.0

        if strategy_name == "sliding_window":
            # 슬라이딩 윈도우: 최근 N개 메시지만 유지 (심플하지만 채팅에 효과적)
            window_size = int(self.get_param("window_size", state, 20))
            if len(state.messages) > window_size:
                state.messages = state.messages[-window_size:]
                results["compacted"] = True
                logger.info("[Context] SlidingWindow: kept last %d messages", window_size)
        elif strategy_name == "context_collapse_overlay":
            # Claude Code L4 — 토큰 압력이 임계 이상이면 오래된 메시지를 pd_stores["history"]
            # 에 보존하고 messages 는 [first, overlay_marker, *last_N] 로 축소.
            # 에이전트는 fetch_pd(kind='history', id='msg_<iter>_<idx>') 로 복원 가능.
            collapse_threshold = self.get_param("context_collapse_threshold", state, 90) / 100.0
            keep_tail = int(self.get_param("context_collapse_keep_tail", state, 3))
            if budget_used > collapse_threshold and len(state.messages) > keep_tail + 1:
                head = state.messages[0]
                tail = state.messages[-keep_tail:]
                old = state.messages[1:-keep_tail]
                # 원본 보존
                iter_no = state.loop_iteration
                preserved_ids: list[str] = []
                for i, msg in enumerate(old):
                    rid = f"msg_{iter_no}_{i}"
                    role = msg.get("role", "?") if isinstance(msg, dict) else "?"
                    preview_line = f"({role}) " + (
                        str(msg.get("content", ""))[:120] if isinstance(msg, dict) else str(msg)[:120]
                    )
                    # full 은 dict 원본을 JSON 직렬화해 보존 (복원 시 읽기 전용).
                    import json as _json
                    try:
                        full_repr = _json.dumps(msg, ensure_ascii=False, default=str)
                    except Exception:
                        full_repr = str(msg)
                    state.pd_store(
                        kind="history",
                        resource_id=rid,
                        preview=preview_line,
                        full=full_repr,
                        meta={"role": role, "loop_iteration": iter_no, "original_index": i + 1},
                    )
                    preserved_ids.append(rid)
                # overlay 마커 — 에이전트에 "N개 메시지 접힘" 알림 + 복원 경로 힌트
                overlay = {
                    "role": "user",
                    "content": (
                        f"[Context Collapse Overlay — {len(old)}개 중간 메시지가 접힘. "
                        f"원본은 pd_stores['history'] 에 보존. "
                        f"필요하면 fetch_pd(kind='history', id='<위 id>') 호출. "
                        f"첫/마지막 {keep_tail} 개는 보존. "
                        f"접힌 id 목록: {preserved_ids[:10]}" +
                        (f"... (+{len(preserved_ids) - 10})" if len(preserved_ids) > 10 else "") +
                        f"]"
                    ),
                }
                state.messages = [head, overlay] + tail
                results["compacted"] = True
                results["context_collapsed"] = len(preserved_ids)
                logger.info(
                    "[Context] L4 Collapse: %d messages preserved in pd_stores['history'], "
                    "kept first + overlay + last %d",
                    len(preserved_ids), keep_tail,
                )
        elif budget_used > compaction_threshold and len(state.messages) > 4:
            # token_budget(기본, 파괴적): first + last 3
            state.messages = [state.messages[0]] + state.messages[-3:]
            results["compacted"] = True
            logger.info("[Context] Compacted: kept first + last 3 messages")

        results["estimated_tokens"] = estimated_tokens
        results["budget_used"] = round(budget_used, 2)

        logger.info("[Context] tokens=%d, budget=%.0f%%, rag=%d cols",
                    estimated_tokens, budget_used * 100, len(rag_collections))
        return results

    async def _fetch_rag(
        self, collections: list[str], query: str, user_id: str,
        top_k: int = 4, score_threshold: float = 0.2, use_model_prompt: bool = True,
    ) -> str:
        """xgen-documents API로 RAG 검색.

        파라미터는 모두 stage_params override 가능:
          - top_k / score_threshold / use_model_prompt
        임베딩 모델은 컬렉션 생성 시 박혀 있어 자동 사용.
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
                        body = {
                            "collection_name": col_name,
                            "query_text": query,
                            "limit": top_k,
                            "score_threshold": score_threshold,
                            "use_model_prompt": use_model_prompt,
                        }
                        resp = await client.post(
                            f"{docs_url}/api/retrieval/documents/search",
                            json=body,
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
