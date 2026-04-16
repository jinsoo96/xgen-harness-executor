"""
S03 System Prompt — 시스템 프롬프트 조립

섹션 우선순위 기반 조립:
1. Identity (역할/페르소나)
2. Rules (행동 규칙)
3. Tool Index (도구 메타데이터 — progressive disclosure Level 1)
4. RAG Context (검색된 문서)
5. History Summary (이전 대화 요약)
6. Custom Sections (사용자 정의)

s04_tool_index가 metadata에 저장한 rag_collections가 있으면
xgen-documents API를 호출해 RAG 컨텍스트를 가져온다.
"""

import logging
from typing import Optional

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..core.service_registry import get_service_url

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
        return "s03_system_prompt"

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

        # 4. RAG Context — ServiceProvider.documents 우선, httpx 직접 호출 폴백
        rag_collections: list[str] = state.metadata.get("rag_collections", [])
        rag_top_k: int = state.metadata.get("rag_top_k", 4)
        if rag_collections and state.user_input:
            # ResourceRegistry 우선 → ServiceProvider → httpx 직접 호출
            registry = state.metadata.get("resource_registry")
            if registry:
                rag_text = await registry.search_rag(state.user_input, rag_collections, rag_top_k)
            else:
                services = state.metadata.get("services")
                if services and services.documents:
                    rag_text = await self._fetch_rag_via_service(
                        services.documents, state.user_input, rag_collections, rag_top_k,
                    )
                else:
                    rag_text = await self._fetch_rag_context(
                        query=state.user_input, collections=rag_collections, top_k=rag_top_k,
                    )
            if rag_text:
                state.rag_context = rag_text

        if state.rag_context:
            rag_section = f"<reference_documents>\n{state.rag_context}\n</reference_documents>"
            sections.append((SECTION_PRIORITIES["rag"], "rag", rag_section))

        # 5. History Summary (이전 결과)
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
            "rag_collections_used": len(rag_collections),
        }
        logger.info("[System Prompt] %d chars, sections=%s", len(assembled), result["sections"])
        return result

    async def _fetch_rag_via_service(
        self, doc_service, query: str, collections: list[str], top_k: int,
    ) -> str:
        """ServiceProvider.documents를 통한 RAG 검색"""
        all_chunks = []
        for collection in collections:
            try:
                results = await doc_service.search(query, collection, limit=top_k)
                for i, doc in enumerate(results, 1):
                    if isinstance(doc, dict):
                        content = doc.get("content", doc.get("text", ""))
                        source = doc.get("source", doc.get("metadata", {}).get("source", ""))
                        if content:
                            header = f"[{len(all_chunks) + 1}]"
                            if source:
                                header += f" ({source})"
                            all_chunks.append(f"{header}\n{content}")
            except Exception as e:
                logger.warning("[System Prompt] RAG search via service failed: %s", e)

        return "\n\n".join(all_chunks) if all_chunks else ""

    async def _fetch_rag_context(
        self,
        query: str,
        collections: list[str],
        top_k: int = 4,
    ) -> str:
        """xgen-documents API로 벡터 검색하여 RAG 컨텍스트 텍스트 반환"""
        import httpx

        url = f"{get_service_url('xgen-documents')}/api/retrieval/documents/search"
        payload = {
            "query": query,
            "collection_names": collections,
            "top_k": top_k,
        }
        headers = {
            "Content-Type": "application/json",
            "x-user-admin": "true",
            "x-user-superuser": "true",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    logger.warning(
                        "[System Prompt] RAG search failed: %d %s",
                        resp.status_code, resp.text[:200],
                    )
                    return ""

                data = resp.json()
                results = data.get("results", data.get("documents", []))
                if not results:
                    logger.info("[System Prompt] RAG search returned 0 results")
                    return ""

                # 결과를 텍스트로 조립
                chunks = []
                for i, doc in enumerate(results, 1):
                    content = ""
                    if isinstance(doc, dict):
                        content = doc.get("content", doc.get("text", doc.get("page_content", "")))
                        source = doc.get("metadata", {}).get("source", doc.get("source", ""))
                        score = doc.get("score", doc.get("similarity", ""))
                        header = f"[{i}]"
                        if source:
                            header += f" ({source})"
                        if score:
                            header += f" score={score:.3f}" if isinstance(score, float) else f" score={score}"
                        chunks.append(f"{header}\n{content}")
                    elif isinstance(doc, str):
                        chunks.append(f"[{i}]\n{doc}")

                rag_text = "\n\n".join(chunks)
                logger.info(
                    "[System Prompt] RAG: %d results from %s (%d chars)",
                    len(results), collections, len(rag_text),
                )
                return rag_text

        except Exception as e:
            logger.warning("[System Prompt] RAG fetch error: %s", e)
            return ""

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
