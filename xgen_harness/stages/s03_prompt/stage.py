"""
S03 System Prompt — 시스템 프롬프트 조립

섹션 우선순위 기반 조립:
1. Identity (역할/페르소나)
2. Rules (행동 규칙)
3. Planning (사고 모드 지시 — CoT/ReAct, v1.0 흡수)
4. Tool Index (도구 메타데이터 — progressive disclosure Level 1)
5. RAG Context (검색된 문서) — 읽기만, 실행은 s06_context 담당
6. History Summary (이전 대화 요약)
7. Custom Sections (사용자 정의)

v1.0: 구 s05_strategy 의 CoT/ReAct planning_instruction + Strategy 카드 매핑 흡수.
      Capability discovery 는 s04_tool, Intent Routing 은 s06_context 로 분배.
v0.9.0: RAG 검색은 s06_context 가 단독 담당 — 이 Stage 는 state.rag_context 를 읽기만.
"""

import logging

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState

logger = logging.getLogger("harness.stage.system_prompt")

# 섹션 우선순위 (낮을수록 높은 우선순위 → 컨텍스트 압축 시 뒤에서부터 제거)
SECTION_PRIORITIES = {
    "identity": 1,
    "rules": 2,
    "planning": 2.5,  # v1.0: CoT/ReAct 지시 — rules 다음, tools 전 (구 s05_strategy 흡수)
    "tools": 3,
    "rag": 4,
    "history": 5,
    "custom": 6,
    "footer": 7,
}

# ─── 공개 레지스트리 (v1.0 — 박제 풀기) ───────────────────────────────
# 모든 프롬프트 텍스트를 등록 기반으로 관리. 외부 작업자/사용자가 자기 도메인
# 템플릿 주입 가능. 우선순위:
#   1. stage_params 직접 override (가장 강함)
#   2. entry_points (xgen_harness.prompt_templates) 로 등록된 외부 템플릿
#   3. register_*(...) 으로 등록된 in-process 템플릿
#   4. 본 파일에 등록된 기본 템플릿 (가장 약함)

# Identity / Rules / Planning 기본 템플릿 — 키 = 템플릿 이름, 값 = 텍스트.
# 외부 등록 시 같은 키 덮어쓰면 override.
DEFAULT_IDENTITIES: dict[str, str] = {
    "default": (
        "You are a helpful AI assistant. "
        "Answer the user's questions accurately and concisely. "
        "If you need more information, use the available tools to find it."
    ),
}

DEFAULT_RULES: dict[str, str] = {
    "default": (
        "<rules>\n"
        "- Always respond in the same language as the user's input.\n"
        "- When using tools, explain what you're doing and why.\n"
        "- If a tool call fails, try an alternative approach before giving up.\n"
        "- Cite sources when using information from reference documents.\n"
        "- Be concise but thorough.\n"
        "</rules>"
    ),
}

# Thinking mode 템플릿 — 키 = 모드 이름, 값 = planning_instruction 텍스트.
# "none" 은 빈 문자열 → planning 섹션 생략.
THINKING_MODE_TEMPLATES: dict[str, str] = {
    "none": "",
    "cot": (
        "<planning_instruction>\n"
        "Before answering, think step by step about what information you need "
        "and which tools to use. Create a brief plan, then execute it.\n"
        "</planning_instruction>"
    ),
    "react": (
        "<planning_instruction>\n"
        "Use the ReAct (Reason + Act) framework:\n"
        "1. Thought: Analyze the current situation and decide the next action.\n"
        "2. Action: Execute a tool or generate a response.\n"
        "3. Observation: Review the result and decide if more steps are needed.\n"
        "Repeat until the task is complete.\n"
        "</planning_instruction>"
    ),
}

# Strategy 카드 → thinking_mode 매핑. 외부에서 새 카드 등록 시 같이 늘리면 됨.
STRATEGY_CARD_TO_MODE: dict[str, str] = {
    "cot_planner": "cot",
    "react": "react",
    "none": "none",
}


def register_identity(name: str, template: str) -> None:
    """Identity 템플릿 등록. 사용자가 stage_params.identity_template = name 으로 선택."""
    DEFAULT_IDENTITIES[name] = template


def register_rules(name: str, template: str) -> None:
    """Rules 템플릿 등록. 사용자가 stage_params.rules_template = name 으로 선택."""
    DEFAULT_RULES[name] = template


def register_thinking_mode(name: str, template: str, *, card_alias: str | None = None) -> None:
    """Thinking mode 등록. card_alias 주면 Strategy 카드 픽도 자동 매핑.

    예) register_thinking_mode("tree_of_thought", "<planning>...</planning>",
                                card_alias="tot")
        → stage_params.thinking_mode = "tree_of_thought" 로 선택,
          또는 active_strategies[s03_prompt] = "tot" 카드 픽으로 자동 매핑.
    """
    THINKING_MODE_TEMPLATES[name] = template
    if card_alias:
        STRATEGY_CARD_TO_MODE[card_alias] = name


_PROMPT_TEMPLATES_DISCOVERED = False


def _discover_prompt_templates_from_entry_points() -> None:
    """entry_points 그룹 ``xgen_harness.prompt_templates`` 자동 발견. idempotent.

    외부 패키지 등록 예:
      [project.entry-points."xgen_harness.prompt_templates"]
      lotte_pack = "lotte_harness.prompts:register_all"

    register_all() 콜러블이 호출되며, 안에서 register_identity/register_rules/
    register_thinking_mode 를 자유롭게 호출.
    """
    global _PROMPT_TEMPLATES_DISCOVERED
    if _PROMPT_TEMPLATES_DISCOVERED:
        return
    _PROMPT_TEMPLATES_DISCOVERED = True
    try:
        from importlib.metadata import entry_points
    except Exception:
        return
    try:
        eps = entry_points()
        group = "xgen_harness.prompt_templates"
        items = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])  # type: ignore[arg-type]
        for ep in items:
            try:
                fn = ep.load()
                if callable(fn):
                    fn()
            except Exception as e:
                logger.warning("[prompt_templates] entry_point %s 로드 실패: %s", ep.name, e)
    except Exception as e:
        logger.debug("[prompt_templates] entry_points discovery 실패: %s", e)


_discover_prompt_templates_from_entry_points()


class SystemPromptStage(Stage):
    """시스템 프롬프트 섹션 기반 조립"""

    @property
    def stage_id(self) -> str:
        return "s03_prompt"

    @property
    def order(self) -> int:
        # v1.2.0 — s04_tool 가 ingress 의 도구 카탈로그를 먼저 채우도록 s03 을 뒤로 민다.
        # 기존 (order=3) 에서는 _build_tool_index_section 이 항상 빈 tool_index 를 봐서
        # <available_tools> 섹션이 사실상 미렌더 → progressive disclosure 의 system_prompt
        # 측 가시성이 죽어있던 회귀. 이제 s04 → s03 순으로 ingress 가 흐른다.
        return 4

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
            sections.append((SECTION_PRIORITIES["identity"], "identity", self._default_identity(state)))

        # 2. Rules — 기본 행동 규칙 (include_rules=False면 건너뛰기)
        if include_rules:
            sections.append((SECTION_PRIORITIES["rules"], "rules", self._default_rules(state)))

        # 3. Tool Index — Level 1 메타데이터 (progressive disclosure)
        # v1.2.0 — eager (tool_index) + deferred (state.deferred_tools) 두 그룹 표시.
        # eager 는 곧바로 호출 가능 / deferred 는 ToolSearch 로 승격 후 호출.
        deferred_list = getattr(state, "deferred_tools", None) or []
        if state.tool_index or deferred_list:
            tool_section = self._build_tool_index_section(state.tool_index, deferred_list)
            sections.append((SECTION_PRIORITIES["tools"], "tools", tool_section))

        # 4. RAG Context — v0.9.0+: 실행은 s06_context 가 단독 담당.
        # 여기서는 이미 채워진 state.rag_context 를 읽기만 한다.
        # (PHILOSOPHY §2 s03 "비담당" — Documents API 호출 금지)
        if state.rag_context:
            rag_section = f"<reference_documents>\n{state.rag_context}\n</reference_documents>"
            sections.append((SECTION_PRIORITIES["rag"], "rag", rag_section))

        # v1.5.4 — R3 회귀 fix. rag_tool_mode='tool' (default) 일 때 state.rag_context 가
        # 비어 있고 <reference_documents> 섹션이 미렌더되어 LLM 이 "참조 자료 있음" 사실
        # 자체를 모르고 DB 도구로 추론하던 회귀 (사용자 호소: "assort 컬렉션 박았는데
        # rag_search 안 부르고 DB 연결 알려달라고 답"). 박힌 컬렉션 list 를 명시하고
        # 도구 우선 사용 안내를 system_prompt 에 박는다.
        rag_collections_attached = list(
            self.get_param("rag_collections", state, []) or []
        )
        # s04 가 rag_collections 를 stage_param 에 두는 경로 (rag/ontology) — s06 진입 전
        # state.metadata 에 노출됨. 둘 다 fallback.
        if not rag_collections_attached:
            rag_collections_attached = list(
                state.metadata.get("rag_collections") or []
            )
        ontology_collections_attached = list(
            self.get_param("ontology_collections", state, []) or []
        )
        if not ontology_collections_attached:
            ontology_collections_attached = list(
                state.metadata.get("ontology_collections") or []
            )
        if rag_collections_attached or ontology_collections_attached:
            lines: list[str] = ["<reference_resources>"]
            lines.append(
                "사용자가 답변에 참조할 자료를 첨부했습니다. 사용자 질문이 이 자료의 "
                "내용과 관련되어 보이면 다음 도구를 우선 호출해 검색하세요."
            )
            if rag_collections_attached:
                lines.append("- RAG 컬렉션 (rag_search 도구로 검색):")
                for col in rag_collections_attached:
                    lines.append(f"  · {col}")
            if ontology_collections_attached:
                lines.append("- 지식 그래프 (query_graph 도구로 검색):")
                for col in ontology_collections_attached:
                    lines.append(f"  · {col}")
            lines.append("</reference_resources>")
            ref_section = "\n".join(lines)
            # rag 섹션 우선순위와 동일 — 도구 안내 직후, history 직전
            sections.append((SECTION_PRIORITIES["rag"] - 0.1, "reference_resources", ref_section))

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

        # 5.5 Planning (CoT/ReAct, v1.0 흡수 from 구 s05_strategy)
        # 첫 루프에만 주입 (재계획 불필요).
        if state.loop_iteration <= 1:
            thinking_mode = self._resolve_thinking_mode(state)
            planning_instruction = self._build_planning_instruction(thinking_mode, state)
            if planning_instruction:
                sections.append((SECTION_PRIORITIES["planning"], "planning", planning_instruction))

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
            # v1.0 — UI 가시성: 사고 모드 / 인용 모드 결과 노출.
            # _build_planning_instruction 에서 auto → cot/react/none resolve 한 결과를
            # state.metadata 에 박아 두면 더 정확하지만, 우선 thinking_mode_resolved 키로 직접 표시.
            "thinking_mode_resolved": (state.metadata.get("thinking_mode_resolved")
                                       or self._resolve_thinking_mode(state)),
            "citation_mode": citation_mode,
        }
        logger.info("[System Prompt] %d chars, sections=%s, thinking=%s",
                    len(assembled), result["sections"], result.get("thinking_mode_resolved"))
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

    # ---------- Planning / Thinking Mode (v1.0 흡수 from 구 s05_strategy) ----------

    def _resolve_thinking_mode(self, state: PipelineState) -> str:
        """thinking_mode 결정 — Strategy 카드 우선, stage_params.thinking_mode 폴백.

        반환 값: "auto" | "none" | "cot" | "react"
        """
        # 1. Strategy 카드 (active_strategies) 가 picked 됐으면 그 값 매핑 사용
        active = ""
        if hasattr(state, "config") and state.config:
            picked = (state.config.active_strategies or {}).get(self.stage_id)
            if isinstance(picked, str):
                active = picked.strip()
        if active and active in STRATEGY_CARD_TO_MODE:
            return STRATEGY_CARD_TO_MODE[active]

        # 2. stage_params.thinking_mode 폴백 (default=auto)
        return self.get_param("thinking_mode", state, "auto")

    def _build_planning_instruction(self, mode: str, state: PipelineState) -> str:
        """thinking_mode 에 따라 planning_instruction 문자열 생성.

        해석 순서 (위에서부터, 박제 0):
          1. stage_params.planning_instruction_template — 사용자가 raw 텍스트 직접 주입
          2. THINKING_MODE_TEMPLATES[mode] — registry/entry_points 로 등록된 템플릿
          3. mode == "auto" 면 input_complexity → mode 자동 결정 후 다시 lookup
          4. 없으면 빈 문자열 (planning 섹션 생략)
        """
        # 1. stage_params raw override (가장 강함)
        raw_override = self.get_param("planning_instruction_template", state, None)
        if isinstance(raw_override, str) and raw_override.strip():
            return raw_override

        # 2. auto 는 complexity 로 mode 결정
        if mode == "auto":
            complexity = state.metadata.get("input_complexity", "moderate")
            if complexity == "simple":
                mode = "none"
            elif complexity == "complex":
                mode = "react"
            else:
                mode = "cot"
            logger.info("[Prompt] thinking_mode auto → '%s' (complexity=%s)", mode, complexity)

        # 3. registry lookup (사용자/외부 등록 + 기본)
        return THINKING_MODE_TEMPLATES.get(mode, "")

    def _default_identity(self, state: PipelineState | None = None) -> str:
        """Identity 텍스트 — registry + stage_params override 지원 (박제 0)."""
        if state is not None:
            raw = self.get_param("identity_template_raw", state, None)
            if isinstance(raw, str) and raw.strip():
                return raw
            name = self.get_param("identity_template", state, "default")
        else:
            name = "default"
        return DEFAULT_IDENTITIES.get(name, DEFAULT_IDENTITIES["default"])

    def _default_rules(self, state: PipelineState | None = None) -> str:
        """Rules 텍스트 — registry + stage_params override 지원 (박제 0)."""
        if state is not None:
            raw = self.get_param("rules_template_raw", state, None)
            if isinstance(raw, str) and raw.strip():
                return raw
            name = self.get_param("rules_template", state, "default")
        else:
            name = "default"
        return DEFAULT_RULES.get(name, DEFAULT_RULES["default"])

    def _build_tool_index_section(
        self,
        tool_index: list[dict],
        deferred_list: list[dict] | None = None,
    ) -> str:
        """Progressive Disclosure Level 1 (v1.2.0 Claude Code 정합).

        eager 도구는 ``<available_tools>`` 섹션에 즉시 호출 가능 도구로 노출.
        deferred 도구가 있으면 ``<deferred_tools>`` 섹션을 별도 추가 — 이름만
        나열하고 LLM 이 ``ToolSearch(names=[...])`` 빌트인으로 승격해야 호출 가능.
        """
        lines = ["<available_tools>"]
        for tool in tool_index or []:
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

        if deferred_list:
            lines.append("\n<deferred_tools>")
            lines.append(
                "These tools are NOT loaded yet — schemas are hidden to save context. "
                "Call ToolSearch(names=[\"tool1\",\"tool2\"]) to load specific tools "
                "before invoking them. Only load what you actually need."
            )
            for tool in deferred_list:
                name = tool.get("name", "unknown")
                desc = tool.get("description", "")
                line = f"- {name}: {desc}" if desc else f"- {name}"
                lines.append(line)
            lines.append("</deferred_tools>")

        return "\n".join(lines)

    def list_strategies(self) -> list[StrategyInfo]:
        # v1.4.0 — 사용자 픽 카드 hide. 사고 모드는 LLM 자율.
        # 코드 (section_priority/cot_planner/react/none) 보존 — active_strategies 직접 셋 가능.
        return []
