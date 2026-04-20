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

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState

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
        citation_enabled: bool = self.get_param("citation_enabled", state, False)
        if citation_enabled:
            citation_instructions = (
                "<citation_instructions>\n"
                "When referencing information from provided documents, cite your sources "
                "using [DOC_1], [DOC_2] format. Each citation should correspond to the "
                "numbered document tags in the reference materials. Always include citations "
                "when stating facts derived from the provided documents.\n"
                "</citation_instructions>"
            )
            sections.append((SECTION_PRIORITIES["rules"] + 0.5, "citation", citation_instructions))

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
