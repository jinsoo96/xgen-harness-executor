"""
S03 System Prompt — 시스템 프롬프트 조립

섹션 우선순위 기반 조립:
1. Identity (역할/페르소나)
2. Rules (행동 규칙)
3. Tool Index (도구 메타데이터 — progressive disclosure Level 1)
4. RAG Context (검색된 문서) — 읽기만, 실행은 s06_context 담당
5. History Summary (이전 대화 요약)
6. Custom Sections (사용자 정의)

v0.9.0: RAG 검색은 s06_context 가 단독 담당 — 이 Stage 는 state.rag_context 를 읽기만.
(docs/harness/00-PHILOSOPHY.md §2 s03 "비담당" 참조)
"""

import logging

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState

logger = logging.getLogger("harness.stage.system_prompt")

# 섹션 우선순위 (낮을수록 높은 우선순위 → 컨텍스트 압축 시 뒤에서부터 제거)
SECTION_PRIORITIES = {
    "identity": 1,
    "rules": 2,
    "tools": 3,
    "rag": 4,
    "history": 5,
    "custom": 6,
    "footer": 7,
}


class SystemPromptStage(Stage):
    """시스템 프롬프트 섹션 기반 조립"""

    @property
    def stage_id(self) -> str:
        return "s03_prompt"

    @property
    def order(self) -> int:
        return 3

    async def execute(self, state: PipelineState) -> dict:
        config = state.config
        sections: list[tuple[int, str, str]] = []  # (priority, name, content)

        # stage_params에서 설정 읽기 (3-level fallback)
        system_prompt_override = self.get_param(
            "system_prompt", state, config.system_prompt if config else ""
        )
        include_rules: bool = self.get_param("include_rules", state, True)

        # 프롬프트 스토어에서 선택된 프롬프트 내용 가져오기
        # stage_params.prompt_content에 프론트에서 미리 복사해둔 내용이 있음
        prompt_content = self.get_param("prompt_content", state, None)
        if prompt_content:
            system_prompt_override = prompt_content

        # 1. Identity — 사용자 지정 시스템 프롬프트
        if system_prompt_override:
            sections.append((SECTION_PRIORITIES["identity"], "identity", system_prompt_override))
        else:
            sections.append((SECTION_PRIORITIES["identity"], "identity", self._default_identity()))

        # 2. Rules — 기본 행동 규칙 (include_rules=False면 건너뛰기)
        if include_rules:
            sections.append((SECTION_PRIORITIES["rules"], "rules", self._default_rules()))

        # 3. Tool Index — Level 1 메타데이터 (progressive disclosure)
        if state.tool_index:
            tool_section = self._build_tool_index_section(state.tool_index)
            sections.append((SECTION_PRIORITIES["tools"], "tools", tool_section))

        # 4. RAG Context — v0.9.0+: 실행은 s06_context 가 단독 담당.
        # 여기서는 이미 채워진 state.rag_context 를 읽기만 한다.
        # (PHILOSOPHY §2 s03 "비담당" — Documents API 호출 금지)
        if state.rag_context:
            rag_section = f"<reference_documents>\n{state.rag_context}\n</reference_documents>"
            sections.append((SECTION_PRIORITIES["rag"], "rag", rag_section))

        # 5. Citation — 문서 인용 형식 지시
        # citation_mode 우선, 하위 호환으로 citation_enabled 도 여전히 읽습니다.
        #   - off      : 인용 지시 없음
        #   - enabled  : [DOC_n] 인용 형식 권장 (기존 citation_enabled=True 와 동일)
        #   - strict   : enabled 규칙 + 검색 결과에 없는 정보는 답하지 않는다는 강한 규칙 추가
        #   - auto     : v0.11.17+ RAG context 패턴으로 자동 판정 (문서 인용형 → strict, 아니면 off)
        raw_mode = self.get_param("citation_mode", state, None)
        legacy_enabled = bool(self.get_param("citation_enabled", state, False))
        if raw_mode is None:
            citation_mode = "enabled" if legacy_enabled else "off"
        else:
            citation_mode = str(raw_mode).strip().lower() or "off"
            if citation_mode not in ("off", "enabled", "strict", "auto"):
                citation_mode = "enabled" if legacy_enabled else "off"

        # v0.11.17 — auto 모드: RAG context 에서 문서형 신호 감지
        if citation_mode == "auto":
            auto_detected = self._detect_citation_need(state)
            citation_mode = "strict" if auto_detected else "off"
            logger.info("[s03] citation_mode=auto → %s (detected=%s)",
                        citation_mode, auto_detected)

        if citation_mode in ("enabled", "strict"):
            citation_instructions = self.get_param(
                "citation_instructions_template", state, None
            ) or (
                "<citation_instructions>\n"
                "When referencing information from provided documents, cite your sources "
                "using [DOC_1], [DOC_2] format. Each citation should correspond to the "
                "numbered document tags in the reference materials. Always include citations "
                "when stating facts derived from the provided documents.\n"
                "</citation_instructions>"
            )
            sections.append((SECTION_PRIORITIES["rules"] + 0.5, "citation", citation_instructions))

        if citation_mode == "strict":
            # 폴백 멘트의 응답 언어는 호출자가 지정 가능. 미지정 시 영어로만 규칙 서술.
            strict_guard = self.get_param(
                "grounding_rules_template", state, None
            ) or (
                "<grounding_rules>\n"
                "Only answer using information present in <reference_documents>. "
                "If the answer cannot be derived from the provided documents, "
                "state that the information is not available in the provided materials "
                "and do not fabricate.\n"
                "</grounding_rules>"
            )
            sections.append((SECTION_PRIORITIES["rules"] + 0.6, "grounding", strict_guard))

        # 6. History Summary (이전 결과)
        if state.previous_results:
            history = "\n---\n".join(state.previous_results[-3:])  # 최근 3개
            sections.append((
                SECTION_PRIORITIES["history"],
                "history",
                f"<previous_results>\n{history}\n</previous_results>",
            ))

        # 조립: 우선순위 순서대로
        sections.sort(key=lambda x: x[0])
        assembled = "\n\n".join(content for _, _, content in sections)
        state.system_prompt = assembled

        result = {
            "prompt_chars": len(assembled),
            "sections": [name for _, name, _ in sections],
            "message_count": len(state.messages),
            "rag_included": bool(state.rag_context),
        }
        logger.info("[System Prompt] %d chars, sections=%s", len(assembled), result["sections"])
        return result

    # 기본 도메인 토큰 — 일반 명사만. 회사/프로젝트 고유명사는 포함하지 않는다.
    # 이식 측이 도메인 특화 토큰을 사용하려면 stage_params 로 override:
    #   citation_auto_doc_tokens: ["규정", "지침", ...]
    #   citation_auto_prod_tokens: ["stock", ...]
    _DEFAULT_DOC_TOKENS: tuple[str, ...] = (
        "doc", "document", "report", "regulation", "policy", "manual",
        "rule", "guide", "spec", "pdf", "hwp", "docx", "pptx",
    )
    _DEFAULT_PROD_TOKENS: tuple[str, ...] = (
        "product", "commerce", "stock", "inventory", "catalog", "sku",
        "price", "item", "sales", "csv", "json", "xlsx",
    )

    def _detect_citation_need(self, state: PipelineState) -> bool:
        """v0.11.17 — 도메인 자동 감지 (auto-router, 실험적).

        s03 는 s06 RAG 주입 전 실행되므로 rag_context 는 보통 빔. 따라서
        **collection 이름 + stage_params.s06_context.rag_collections** 를 먼저 감지.

        휴리스틱 우선순위:
          1. Collection 이름 토큰 (중립 명사만; override 가능)
          2. RAG context 에 파일 확장자 (.pdf vs .csv) — rag_context 주입된 경우만
          3. 내용 신호 (연도·metadata) fallback

        **확장 지점**:
          - `citation_auto_doc_tokens` / `citation_auto_prod_tokens` stage_param
            으로 회사·언어 특화 토큰 주입 (기본값과 OR 결합).

        본 판정은 휴리스틱이라 완전하지 않음. 사용자가 명시 off/strict 주면 override.
        """
        import re as _re

        # 1차 — collection 이름 토큰
        rag_collections: list[str] = self.get_param("rag_collections", state, []) or []
        if not rag_collections:
            rag_collections = (state.metadata or {}).get("rag_collections", []) or []
        col_text = " ".join(str(c).lower() for c in rag_collections)

        extra_doc = self.get_param("citation_auto_doc_tokens", state, []) or []
        extra_prod = self.get_param("citation_auto_prod_tokens", state, []) or []
        doc_tokens = tuple(self._DEFAULT_DOC_TOKENS) + tuple(str(t).lower() for t in extra_doc)
        prod_tokens = tuple(self._DEFAULT_PROD_TOKENS) + tuple(str(t).lower() for t in extra_prod)

        doc_col_match = sum(1 for t in doc_tokens if t in col_text)
        prod_col_match = sum(1 for t in prod_tokens if t in col_text)
        if doc_col_match + prod_col_match > 0:
            decision = doc_col_match >= prod_col_match and doc_col_match >= 1
            logger.info(
                "[s03] auto-detect (collection): doc=%d prod=%d → %s",
                doc_col_match, prod_col_match,
                "strict" if decision else "off",
            )
            return decision

        rag_ctx = state.rag_context or ""
        if not rag_ctx:
            return False
        # 1차 — 파일 확장자 signal (가장 robust)
        doc_ext = (
            rag_ctx.count(".pdf") + rag_ctx.count(".docx")
            + rag_ctx.count(".hwp") + rag_ctx.count(".pptx")
        )
        struct_ext = (
            rag_ctx.count(".csv") + rag_ctx.count(".json")
            + rag_ctx.count(".xlsx") + rag_ctx.count(".parquet")
            + rag_ctx.count(".tsv")
        )
        if doc_ext + struct_ext > 0:
            decision = doc_ext >= struct_ext and doc_ext >= 1
            logger.info(
                "[s03] auto-detect (ext): doc=%d struct=%d → %s",
                doc_ext, struct_ext, "strict" if decision else "off",
            )
            return decision

        # 2차 fallback — 내용 신호
        year_pat = len(_re.findall(r"\d{4}년도?[\s_\-][가-힣]{2,}", rag_ctx))
        meta_signal = (
            rag_ctx.count("Document-Metadata")
            + rag_ctx.count("작성자")
            + rag_ctx.count("제목:")
            + rag_ctx.count("마지막 수정자")
        )
        product_signal = (
            len(_re.findall(r"G\d{4,}", rag_ctx))
            + rag_ctx.count("원")
            + rag_ctx.count("₩")
        )
        doc_score = year_pat * 2 + meta_signal * 3
        prod_score = product_signal
        decision = doc_score >= 1 and doc_score >= prod_score
        logger.info(
            "[s03] auto-detect (content): doc=%d (year=%d meta=%d) prod=%d → %s",
            doc_score, year_pat, meta_signal, prod_score,
            "strict" if decision else "off",
        )
        return decision

    def _default_identity(self) -> str:
        return (
            "You are a helpful AI assistant. "
            "Answer the user's questions accurately and concisely. "
            "If you need more information, use the available tools to find it."
        )

    def _default_rules(self) -> str:
        return (
            "<rules>\n"
            "- Always respond in the same language as the user's input.\n"
            "- When using tools, explain what you're doing and why.\n"
            "- If a tool call fails, try an alternative approach before giving up.\n"
            "- Cite sources when using information from reference documents.\n"
            "- Be concise but thorough.\n"
            "</rules>"
        )

    def _build_tool_index_section(self, tool_index: list[dict]) -> str:
        """Progressive Disclosure Level 1: 도구 메타데이터만 포함"""
        lines = ["<available_tools>"]
        for tool in tool_index:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            category = tool.get("category", "")
            line = f"- {name}: {desc}"
            if category:
                line += f" [{category}]"
            lines.append(line)

        lines.append("</available_tools>")
        lines.append(
            "\nTo learn more about a specific tool's parameters, "
            "use the discover_tools function with the tool name."
        )
        return "\n".join(lines)

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("section_priority", "우선순위 기반 섹션 조립", is_default=True),
            StrategyInfo("simple", "단순 문자열 연결"),
        ]
