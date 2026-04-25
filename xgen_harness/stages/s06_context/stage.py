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

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState
from ...core.service_registry import get_service_url

logger = logging.getLogger("harness.stage.context")

CHARS_PER_TOKEN = 3  # 평균 추정 (영어 ≈ 4, 한국어 ≈ 1.5~2). stage_param chars_per_token 으로 override.


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
        # v0.25.2 — config.rag_collections 폴백은 **autonomous 모드에서만** 허용.
        # 사용자가 UI 에서 컬렉션을 명시 비워 저장한 경우 (selected/off 모드), 자동 폴백으로
        # 전체 문서 기반 답변이 나가면 안 된다. v0.25.3 은 리터럴 비교 대신
        # HarnessConfig.is_autonomous() 헬퍼로 도메인 언어 캡슐화.
        if not rag_collections and hasattr(config, 'rag_collections'):
            is_auto = bool(getattr(config, 'is_autonomous', lambda: True)())
            if is_auto:
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
            from ...events.types import StageSubstepEvent as _Sub
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
                from ...utils.docs import extract_source, extract_text, extract_score
                score_threshold = float(self.get_param("score_threshold", state, 0.0))
                # metadata_filter — xgen-documents DocumentSearchRequest.filter 로 전달.
                # 우선순위: stage_params.metadata_filter (명시) > state.metadata.auto_metadata_filter (RR2 intent routing).
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
                if metadata_filter is None:
                    # RR2 intent routing 이 s05 에서 세팅했을 수 있음 (fallback)
                    auto = state.metadata.get("auto_metadata_filter") if hasattr(state, "metadata") else None
                    if isinstance(auto, dict) and auto:
                        metadata_filter = auto
                        logger.info("[Context] using auto_metadata_filter from intent routing: %s", auto)
                # v0.26.1 — `files` stage_param 자동 라우팅 (frontend 의 files multi_select UI 가
                # 의미 있게 동작하도록). 사용자가 selected_files 에 file_name 들을 박으면
                # metadata_filter 의 `file_name` 키로 합쳐서 검색 범위를 자동 좁힘.
                # 이전엔 files 필드가 dead 였음 (엔진이 read 안 함) — 이제 진짜 wiring.
                files_selected = self.get_param("files", state, []) or []
                if isinstance(files_selected, list) and files_selected:
                    if metadata_filter is None:
                        metadata_filter = {}
                    elif not isinstance(metadata_filter, dict):
                        metadata_filter = {}
                    # 기존 file_name 키와 union (사용자가 textarea 로 직접 박은 것 보존)
                    existing = metadata_filter.get("file_name", []) if isinstance(metadata_filter.get("file_name"), list) else []
                    merged = list({*existing, *files_selected})
                    metadata_filter["file_name"] = merged
                    logger.info("[Context] files routed to metadata_filter.file_name: %d items", len(merged))
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
                # v0.11.25 — httpx 직접 호출 폴백 제거. 엔진은 xgen-documents API 스키마를
                # 모른다. DocumentService 가 ServiceProvider 로 주입되지 않았으면 RAG 는
                # graceful skip — 외부 조직이 독립 실행 시 자기 ServiceProvider 를 붙이거나
                # RAG 를 비활성화하면 된다.
                logger.info("[Context] DocumentService not injected — RAG search skipped")
                rag_context = ""
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
                # v0.11.18 — rag_ingestion_mode 로 system prompt 주입 여부 제어.
                #   system_prompt (기본): 현재 동작 — RAG 를 system prompt 에 주입 (LLM 이 즉시 활용)
                #   tool_only: system prompt 주입 skip. LLM 은 rag_search 도구 호출로만 RAG 접근.
                #              → tool_result 누적 → L3 microcompact 발동 조건 충족.
                #   both: 현재 `rag_tool_mode=both` 와 정합. system prompt 에도 있고 도구로도 접근.
                rag_ing_mode = str(self.get_param("rag_ingestion_mode", state, "system_prompt") or "system_prompt").strip().lower()
                if rag_ing_mode not in ("system_prompt", "tool_only", "both"):
                    rag_ing_mode = "system_prompt"
                # rag_tool_mode 가 'tool' 이면 ingestion mode 가 system_prompt 라도 tool_only 로 정정
                # (사용자 의도: RAG 를 도구로만 쓰겠다).
                rag_tool_mode_val = str(self.get_param("rag_tool_mode", state, "both") or "both").strip().lower()
                if rag_tool_mode_val == "tool" and rag_ing_mode == "system_prompt":
                    rag_ing_mode = "tool_only"
                    logger.info("[Context] rag_tool_mode=tool → rag_ingestion_mode auto-corrected to tool_only")

                if rag_ing_mode in ("system_prompt", "both"):
                    state.system_prompt = f"{state.system_prompt}\n\n{rag_context}"
                    # enhance_prompt — RAG 컨텍스트 뒤에 사용자 지정 "응답 향상" 지시를 붙입니다.
                    enhance_prompt = str(self.get_param("enhance_prompt", state, "") or "").strip()
                    if enhance_prompt:
                        state.system_prompt = (
                            f"{state.system_prompt}\n\n"
                            f"<enhance_prompt>\n{enhance_prompt}\n</enhance_prompt>"
                        )
                        results["enhance_prompt_applied"] = True
                    results["rag_chunks"] = rag_context.count("[")  # 대략적 청크 수
                    results["rag_collections"] = len(rag_collections)
                    logger.info("[Context] RAG: %d collections, added to system prompt (mode=%s)",
                                len(rag_collections), rag_ing_mode)
                else:
                    # tool_only — system prompt 주입 skip. state.rag_context 는 남겨둬 s04 RAGSearchTool 이 참조.
                    # 결과 카운트 표시는 유지 (추적용).
                    results["rag_chunks"] = rag_context.count("[")
                    results["rag_collections"] = len(rag_collections)
                    results["rag_ingestion_mode"] = "tool_only"
                    logger.info("[Context] RAG: %d collections, tool_only mode (system prompt skip)",
                                len(rag_collections))
            from ...events.types import StageSubstepEvent as _Sub2
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
        # v0.11.20 — context_window 는 getattr 로 방어 (HarnessConfig 이전 버전 호환).
        context_window = self.get_param(
            "context_window", state, getattr(config, "context_window", 200_000)
        )
        max_tokens = getattr(config, "max_tokens", 8192) if config else 8192
        available_tokens = context_window - max_tokens

        if config and getattr(config, "thinking_enabled", False):
            available_tokens -= getattr(config, "thinking_budget_tokens", 0)

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

        # chars_per_token override 가능 (영어/한국어 토큰 비율이 달라서).
        chars_per_token = int(self.get_param("chars_per_token", state, CHARS_PER_TOKEN))
        if chars_per_token < 1:
            chars_per_token = CHARS_PER_TOKEN
        estimated_tokens = total_chars // chars_per_token
        budget_used = estimated_tokens / available_tokens if available_tokens > 0 else 1.0

        # 압축 전략 디스패치
        #   token_budget (기본):  first + last_3 유지. 파괴적 (원본 소실).
        #   sliding_window:        최근 N개 유지. 파괴적.
        #   microcompact (L3):     오래된 tool_result 만 placeholder 교체. 비파괴 (pd_stores 복원).
        #   context_collapse_overlay (L4): 중간 메시지 overlay 교체. 비파괴.
        #   autocompact_llm (L5):  child LLM 9-section 요약으로 교체. 비파괴.
        #   cascade (v0.11.15+):   임계별 L3 → L4 → L5 순 자동 전환. 현재 turn 에 1개만 발동.
        strategy_name = self.get_param("strategy", state, "token_budget")
        compaction_threshold = self.get_param("compaction_threshold", state, 80) / 100.0

        # v0.11.21 — 외부 기여자가 register_strategy("s06_context","compactor",...) 로
        # 교체할 수 있도록 resolver 경로를 선행 조회. AdvancedContextCompactor 인스턴스가
        # 반환되면 자체 apply() 에 전적으로 위임하고 inline if/elif 는 건너뛴다.
        handled_by_strategy = False
        try:
            from ...core.strategy_resolver import StrategyResolver
            from ..strategies.compactor import AdvancedContextCompactor
            resolver = StrategyResolver.default()
            compactor = resolver.resolve("s06_context", "compactor", strategy_name)
            if isinstance(compactor, AdvancedContextCompactor):
                await compactor.apply(state=state, stage=self, budget_used=budget_used, results=results)
                handled_by_strategy = True
        except Exception as e:
            logger.warning("[Context] strategy resolver dispatch 실패 (%s): %s", strategy_name, e)

        if handled_by_strategy:
            pass  # AdvancedContextCompactor 가 results 에 이미 반영
        elif strategy_name == "sliding_window":
            # 슬라이딩 윈도우: 최근 N개 메시지만 유지 (심플하지만 채팅에 효과적)
            window_size = int(self.get_param("window_size", state, 20))
            if len(state.messages) > window_size:
                state.messages = state.messages[-window_size:]
                results["compacted"] = True
                logger.info("[Context] SlidingWindow: kept last %d messages", window_size)
        elif strategy_name == "microcompact":
            self.try_microcompact(state, budget_used, results)
        elif strategy_name == "autocompact_llm":
            await self.try_autocompact(state, budget_used, results)
        elif strategy_name == "context_collapse_overlay":
            self.try_context_collapse(state, budget_used, results)
        elif strategy_name == "cascade":
            await self.try_cascade(state, budget_used, results)
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

    # ── L3 / L4 / L5 / Cascade helper methods ──

    async def try_cascade(
        self, state: PipelineState, budget_used: float, results: dict,
    ) -> None:
        """Claude Code Cascade — 압력에 따라 L3 → L4 → L5 자동 선택. 한 턴에 하나만 발동.

        - < l3_th:  pass (L1 preview 는 s08 이 항상 수행)
        - >= l3_th: L3 microcompact (경량)
        - >= l4_th: L4 context_collapse_overlay (중량)
        - >= l5_th: L5 autocompact_llm (중량, 실패 시 회로 차단)

        v0.11.16 — L3 기본 70 → 80 (baseline token_budget 의 compaction_threshold 와 동기).
        Pilot #11 에서 조기 발동 품질 악화 관측 → baseline compact 시점 이후만 개입.
        v0.11.20 — helper 에 직접 threshold 전달 (state.metadata 임시 키 제거, leak 방지).
        """
        l3_th = self.get_param("cascade_l3_threshold", state, 80) / 100.0
        l4_th = self.get_param("cascade_l4_threshold", state, 90) / 100.0
        l5_th = self.get_param("cascade_l5_threshold", state, 97) / 100.0
        cascade_applied: list[str] = []
        if budget_used >= l3_th:
            pre_mc = results.get("microcompacted", 0)
            self.try_microcompact(state, budget_used, results, threshold_override=l3_th)
            if results.get("microcompacted", 0) > pre_mc:
                cascade_applied.append("L3")
        if budget_used >= l5_th:
            pre_ac = results.get("autocompacted", 0)
            await self.try_autocompact(state, budget_used, results, threshold_override=l5_th)
            if results.get("autocompacted", 0) > pre_ac:
                cascade_applied.append("L5")
        elif budget_used >= l4_th:
            pre_cc = results.get("context_collapsed", 0)
            self.try_context_collapse(state, budget_used, results, threshold_override=l4_th)
            if results.get("context_collapsed", 0) > pre_cc:
                cascade_applied.append("L4")
        if cascade_applied:
            results["cascade_applied"] = cascade_applied
            logger.info("[Context] Cascade dispatched: %s (budget=%.0f%%)",
                        "+".join(cascade_applied), budget_used * 100)

    def try_microcompact(
        self, state: PipelineState, budget_used: float, results: dict,
        threshold_override: float | None = None,
    ) -> None:
        """L3 — 오래된 tool_result 블록을 placeholder 로 교체. 원본은 pd_stores['tool_result'] 보존.

        threshold_override: cascade 에서 전달하는 임계(0~1). None 이면 stage_param 의 기본값 사용.
        """
        mc_keep = int(self.get_param("microcompact_keep_recent", state, 5))
        if threshold_override is not None:
            mc_threshold = float(threshold_override)
        else:
            mc_threshold = self.get_param("microcompact_threshold", state, 75) / 100.0
        if budget_used <= mc_threshold:
            return
        tool_refs: list[tuple[int, int, str]] = []
        for mi, msg in enumerate(state.messages):
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if not isinstance(content, list):
                continue
            for bi, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "") or f"unknown_{mi}_{bi}"
                    tool_refs.append((mi, bi, tid))
        if len(tool_refs) <= mc_keep:
            return
        to_replace = tool_refs[:-mc_keep]
        replaced = 0
        for mi, bi, tid in to_replace:
            msg = state.messages[mi]
            content = msg.get("content") or []
            if isinstance(content, list) and 0 <= bi < len(content):
                original = content[bi]
                if isinstance(original, dict) and original.get("type") == "tool_result":
                    has_pd = state.pd_fetch("tool_result", tid) is not None
                    placeholder_text = (
                        f"[Microcompact — 오래된 tool_result. "
                        f"fetch_pd(kind='tool_result', id='{tid}') 로 조회]"
                        if has_pd else
                        f"[Microcompact — tool_result omitted (id={tid})]"
                    )
                    content[bi] = {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": placeholder_text,
                    }
                    replaced += 1
        if replaced:
            results["compacted"] = True
            results["microcompacted"] = replaced
            logger.info(
                "[Context] L3 Microcompact: %d tool_results 교체 (최근 %d 유지)",
                replaced, mc_keep,
            )

    def try_context_collapse(
        self, state: PipelineState, budget_used: float, results: dict,
        threshold_override: float | None = None,
    ) -> None:
        """L4 — 오래된 메시지를 overlay 로 교체하고 원본은 pd_stores['history'] 에 보존.

        threshold_override: cascade 에서 전달하는 임계(0~1). None 이면 stage_param 의 기본값 사용.
        """
        if threshold_override is not None:
            collapse_threshold = float(threshold_override)
        else:
            collapse_threshold = self.get_param("context_collapse_threshold", state, 90) / 100.0
        keep_tail = int(self.get_param("context_collapse_keep_tail", state, 3))
        if budget_used <= collapse_threshold or len(state.messages) <= keep_tail + 1:
            return
        head = state.messages[0]
        tail = state.messages[-keep_tail:]
        old = state.messages[1:-keep_tail]
        iter_no = state.loop_iteration
        preserved_ids: list[str] = []
        import json as _json
        for i, msg in enumerate(old):
            rid = f"msg_{iter_no}_{i}"
            role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            preview_line = f"({role}) " + (
                str(msg.get("content", ""))[:120] if isinstance(msg, dict) else str(msg)[:120]
            )
            try:
                full_repr = _json.dumps(msg, ensure_ascii=False, default=str)
            except Exception as e:
                logger.debug("[Context] L4 collapse 메시지 %d JSON 직렬화 실패, str() fallback: %s", i, e)
                full_repr = str(msg)
            state.pd_store(
                kind="history",
                resource_id=rid,
                preview=preview_line,
                full=full_repr,
                meta={"role": role, "loop_iteration": iter_no, "original_index": i + 1},
            )
            preserved_ids.append(rid)
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

    async def try_autocompact(
        self, state: PipelineState, budget_used: float, results: dict,
        threshold_override: float | None = None,
    ) -> None:
        """L5 — child LLM 9-section summary 로 교체. 원본은 pd_stores['history'] 보존. 회로 차단기.

        threshold_override: cascade 에서 전달하는 임계(0~1). None 이면 stage_param 의 기본값 사용.
        """
        if threshold_override is not None:
            auto_threshold = float(threshold_override)
        else:
            auto_threshold = self.get_param("autocompact_threshold", state, 87) / 100.0
        keep_tail = int(self.get_param("autocompact_keep_tail", state, 3))
        failures = int(state.metadata.get("autocompact_failures", 0))
        if failures >= 3:
            logger.warning("[Context] L5 Autocompact circuit-breaker tripped (failures=%d), skip", failures)
            return
        if budget_used <= auto_threshold or len(state.messages) <= keep_tail + 1:
            return
        head = state.messages[0]
        tail = state.messages[-keep_tail:]
        old = state.messages[1:-keep_tail]
        iter_no = state.loop_iteration
        preserved_ids: list[str] = []
        import json as _json
        for i, msg in enumerate(old):
            rid = f"auto_{iter_no}_{i}"
            role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            try:
                full_repr = _json.dumps(msg, ensure_ascii=False, default=str)
            except Exception as e:
                logger.debug("[Context] L5 autocompact 메시지 %d JSON 직렬화 실패, str() fallback: %s", i, e)
                full_repr = str(msg)
            state.pd_store(
                kind="history", resource_id=rid,
                preview=f"({role}) ...", full=full_repr,
                meta={"role": role, "loop_iteration": iter_no,
                      "original_index": i + 1, "compaction": "autocompact_llm"},
            )
            preserved_ids.append(rid)
        summary_text = await self._autocompact_summarize(state, old)
        if summary_text:
            summary_msg = {
                "role": "user",
                "content": (
                    "[Autocompact Summary — child agent 9-section:]\n" + summary_text +
                    f"\n\n[원본 {len(preserved_ids)}개는 pd_stores['history'] 에 보존. "
                    f"필요시 fetch_pd(kind='history', id='auto_<iter>_<idx>')]"
                ),
            }
            state.messages = [head, summary_msg] + tail
            results["compacted"] = True
            results["autocompacted"] = len(preserved_ids)
            logger.info(
                "[Context] L5 Autocompact: %d messages → summary, kept first + last %d",
                len(preserved_ids), keep_tail,
            )
        else:
            state.metadata["autocompact_failures"] = failures + 1
            logger.warning("[Context] L5 Autocompact 실패 %d/3", failures + 1)

    async def _autocompact_summarize(self, state: PipelineState, old_messages: list[dict]) -> str:
        """Claude Code L5 child agent 9-section summary.

        state.provider 가 있으면 그걸로 LLM 호출. 없으면 규칙 기반 fallback summary.
        9 sections: Primary Request, Key Decisions, Tools Used, Errors/Fixes,
        Files Touched, Data Mentioned, User Preferences, Open Issues, Next Steps.
        """
        # 메시지들을 LLM 에 던질 텍스트로 직렬화 (경량)
        lines = []
        for i, msg in enumerate(old_messages):
            if not isinstance(msg, dict): continue
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                # content blocks: text/tool_use/tool_result — 텍스트만 추출
                parts = []
                for b in content:
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            parts.append(str(b.get("text", "")))
                        elif b.get("type") == "tool_use":
                            parts.append(f"[tool_use: {b.get('name','?')}]")
                        elif b.get("type") == "tool_result":
                            parts.append(f"[tool_result: {str(b.get('content',''))[:200]}]")
                content_str = " ".join(parts)
            else:
                content_str = str(content)
            lines.append(f"[{i+1}] {role}: {content_str[:800]}")
        conversation = "\n".join(lines)

        prompt = (
            "아래 대화 이력을 9 섹션 구조로 요약하라. 한국어 응답, 각 섹션 1~3 줄.\n\n"
            "## Primary Request\n## Key Decisions\n## Tools Used\n## Errors/Fixes\n"
            "## Files Touched\n## Data Mentioned\n## User Preferences\n## Open Issues\n## Next Steps\n\n"
            "--- 대화 시작 ---\n"
            f"{conversation}\n"
            "--- 대화 끝 ---"
        )

        provider = getattr(state, "provider", None)
        if provider is None:
            # Fallback: 규칙 기반 초간단 summary
            roles = [m.get("role","?") for m in old_messages if isinstance(m, dict)]
            return (
                "## Primary Request\n(child LLM 미사용 — rule-based fallback)\n"
                f"## Messages\n총 {len(old_messages)}개 ({dict((r, roles.count(r)) for r in set(roles))})\n"
                "## Note\nprovider 초기화 안 된 상태에서 L5 발동. 정확한 요약은 재실행 권장."
            )

        try:
            # 간단 chat 호출 — stream 없이 non-blocking
            if hasattr(provider, "chat_once"):
                return await provider.chat_once(prompt, max_tokens=500, temperature=0.0)
            # chat_once 없으면 chat() stream 을 수집
            if hasattr(provider, "chat"):
                from ...providers.base import MessageBlock
                result_chunks = []
                async for event in provider.chat(
                    messages=[{"role": "user", "content": prompt}],
                    system="You are a precise summarization agent.",
                    tools=[], model=None, max_tokens=500, temperature=0.0,
                ):
                    if hasattr(event, "text") and event.text:
                        result_chunks.append(event.text)
                return "".join(result_chunks).strip()
        except Exception as e:
            logger.warning("[Context] L5 summarize LLM 호출 실패: %s", e)
            return ""
        return ""

    def list_strategies(self) -> list[StrategyInfo]:
        # v0.11.20 — dispatcher 와 완전 동기. stage_config.options 와 이 목록은 단일 진실 원본이어야 함.
        return [
            StrategyInfo("token_budget", "RAG 검색 + 토큰 예산 압축 (파괴적 first+last3)", is_default=True),
            StrategyInfo("sliding_window", "슬라이딩 윈도우 (최근 N개 메시지)"),
            StrategyInfo("microcompact", "L3 Microcompact — 오래된 tool_result 를 placeholder 로 교체 (Claude Code L3)"),
            StrategyInfo("context_collapse_overlay", "L4 Context Collapse — 중간 메시지 overlay, 원본은 pd_stores 보존 (Claude Code L4)"),
            StrategyInfo("autocompact_llm", "L5 Autocompact — child LLM 9-section summary (Claude Code L5)"),
            StrategyInfo("cascade", "Cascade — 압력별 L3→L4→L5 자동 선택 (Claude Code Cascade)"),
        ]
