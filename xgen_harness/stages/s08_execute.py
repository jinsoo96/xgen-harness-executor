"""
S08 Execute — 도구 실행

- pending_tool_calls에 있는 도구 호출을 실행
- read_only 도구: asyncio.gather로 병렬 실행
- write 도구: 순차 실행
- 결과를 tool_results에 적재 → messages에 user 메시지로 추가
- 50K 문자 예산 초과 시 결과 축약
"""

import asyncio
import logging
import traceback
from typing import Any

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..events.types import ToolResultEvent
from ..errors import ToolError, ToolTimeoutError

logger = logging.getLogger("harness.stage.execute")

TOOL_TIMEOUT_DEFAULT = 60.0
RESULT_BUDGET_DEFAULT = 50_000


class ExecuteStage(Stage):
    """도구 실행 스테이지"""

    @property
    def stage_id(self) -> str:
        return "s08_execute"

    @property
    def order(self) -> int:
        return 8

    def should_bypass(self, state: PipelineState) -> bool:
        return not state.pending_tool_calls

    async def execute(self, state: PipelineState) -> dict:
        if not state.pending_tool_calls:
            return {"tools_executed": 0, "bypassed": True}

        # stage_params에서 설정 읽기 (UI 설정 > stage_config 기본값 > 하드코딩)
        self._tool_timeout = self.get_param("timeout", state, TOOL_TIMEOUT_DEFAULT)
        result_budget = self.get_param("result_budget", state, RESULT_BUDGET_DEFAULT)

        tool_calls = state.pending_tool_calls
        strategy_name = self.get_param("strategy", state, "default")

        if strategy_name == "parallel_read":
            results, total_chars = await self._execute_parallel_read(tool_calls, state, result_budget)
        else:
            results, total_chars = await self._execute_sequential(tool_calls, state, result_budget)

        # 도구 결과를 messages에 flush
        state.flush_tool_results()
        state.tools_executed_count += len(results)

        executed_count = sum(1 for r in results if r["success"])
        error_count = sum(1 for r in results if not r["success"])

        logger.info("[Execute] %d tools executed, %d errors (strategy=%s)",
                     executed_count, error_count, strategy_name)
        return {
            "tools_executed": len(results),
            "success_count": executed_count,
            "error_count": error_count,
            "total_chars": total_chars,
            "strategy": strategy_name,
        }

    async def _execute_sequential(
        self, tool_calls: list, state: PipelineState, result_budget: int
    ) -> tuple[list[dict[str, Any]], int]:
        """순차 실행 -- 기본 전략"""
        results: list[dict[str, Any]] = []
        total_chars = 0

        for tc in tool_calls:
            r, chars = await self._execute_single(
                tc.get("tool_use_id", ""), tc.get("tool_name", ""),
                tc.get("tool_input", {}), state, result_budget, total_chars,
            )
            results.append(r)
            total_chars += chars

        return results, total_chars

    async def _execute_parallel_read(
        self, tool_calls: list, state: PipelineState, result_budget: int
    ) -> tuple[list[dict[str, Any]], int]:
        """parallel_read 전략: 읽기 전용 도구는 병렬, 쓰기 도구는 순차.

        is_read_only 판별 순서:
        1. tool_registry에 등록된 Tool 인스턴스의 is_read_only 속성
        2. tool_definitions의 metadata.is_read_only 필드
        3. 이름 기반 휴리스틱 (write 키워드 미포함이면 read_only)
        """
        write_keywords = {"create", "update", "delete", "write", "send", "post", "put", "remove", "insert", "drop"}
        tool_registry = state.metadata.get("tool_registry", {})

        read_calls = []
        write_calls = []

        for tc in tool_calls:
            tool_name = tc.get("tool_name", "")
            is_read_only = None

            # 1. Tool 인스턴스에서 확인
            tool_instance = tool_registry.get(tool_name)
            if tool_instance and hasattr(tool_instance, "is_read_only"):
                is_read_only = tool_instance.is_read_only

            # 2. tool_definitions metadata에서 확인
            if is_read_only is None:
                for td in state.tool_definitions:
                    if td.get("name") == tool_name:
                        meta = td.get("metadata", {})
                        if "is_read_only" in meta:
                            is_read_only = meta["is_read_only"]
                        break

            # 3. 이름 기반 휴리스틱 폴백
            if is_read_only is None:
                name_lower = tool_name.lower()
                is_read_only = not any(kw in name_lower for kw in write_keywords)

            if is_read_only:
                read_calls.append(tc)
            else:
                write_calls.append(tc)

        results: list[dict[str, Any]] = []
        total_chars = 0

        # 읽기 도구 -> asyncio.gather 병렬 실행
        if read_calls:
            async def _run_read(tc):
                return await self._execute_single(
                    tc.get("tool_use_id", ""), tc.get("tool_name", ""),
                    tc.get("tool_input", {}), state, result_budget, 0,
                )

            parallel_results = await asyncio.gather(
                *[_run_read(tc) for tc in read_calls],
                return_exceptions=True,
            )
            for pr in parallel_results:
                if isinstance(pr, Exception):
                    results.append({"tool_name": "unknown", "success": False, "error": str(pr)})
                else:
                    r, chars = pr
                    results.append(r)
                    total_chars += chars

            logger.info("[Execute] parallel_read: %d read tools executed in parallel", len(read_calls))

        # 쓰기 도구 -> 순차 실행 (순서 보존)
        for tc in write_calls:
            r, chars = await self._execute_single(
                tc.get("tool_use_id", ""), tc.get("tool_name", ""),
                tc.get("tool_input", {}), state, result_budget, total_chars,
            )
            results.append(r)
            total_chars += chars

        return results, total_chars

    async def _execute_single(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: dict,
        state: PipelineState,
        result_budget: int,
        current_chars: int,
    ) -> tuple[dict[str, Any], int]:
        """단일 도구 실행 + 결과 축약 + 이벤트 발행. (result_info, chars) 반환."""
        try:
            # Capability 기반 파라미터 자동 보강 — tool_name이 capability에 바인딩됐으면
            # ParameterResolver로 누락된 필수 파라미터를 context에서 채움
            tool_input = await self._enrich_with_capability(tool_name, tool_input, state)

            result_text = await self._execute_tool(tool_name, tool_input, state)

            # 결과 축약 (예산 초과 시)
            chars = len(result_text)
            if current_chars + chars > result_budget:
                remaining = max(0, result_budget - current_chars)
                result_text = result_text[:remaining] + f"\n... (축약됨, 원본 {chars}자)"
                chars = len(result_text)

            state.add_tool_result(tool_use_id, result_text, is_error=False)

            if state.event_emitter:
                await state.event_emitter.emit(ToolResultEvent(
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    result=result_text[:500],
                    is_error=False,
                ))

            return {"tool_name": tool_name, "success": True, "chars": chars}, chars

        except asyncio.TimeoutError:
            error_msg = f"Tool '{tool_name}' timed out after {self._tool_timeout}s"
            state.add_tool_result(tool_use_id, error_msg, is_error=True)
            logger.warning("[Execute] %s", error_msg)
            return {"tool_name": tool_name, "success": False, "error": "timeout"}, 0

        except Exception as e:
            error_msg = f"Tool '{tool_name}' failed: {str(e)}"
            state.add_tool_result(tool_use_id, error_msg, is_error=True)
            logger.error("[Execute] %s\n%s", error_msg, traceback.format_exc())

            if state.event_emitter:
                await state.event_emitter.emit(ToolResultEvent(
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    result=error_msg,
                    is_error=True,
                ))

            return {"tool_name": tool_name, "success": False, "error": str(e)}, 0

    async def _enrich_with_capability(
        self, tool_name: str, tool_input: dict, state: PipelineState
    ) -> dict:
        """tool_name에 바인딩된 CapabilitySpec이 있으면 ParameterResolver로 args 보강.

        - capability_bindings에서 역조회 (tool_name → capability_name)
        - 못 찾으면 tool_input 그대로 반환
        - 보강 과정에서 누락 필수 파라미터가 있으면 MissingParamEvent 이벤트 발행
        """
        try:
            bindings = state.metadata.get("capability_bindings", {}) or {}
            # 역인덱스: capability_name → tool_name
            cap_name = next((c for c, t in bindings.items() if t == tool_name), None)
            if cap_name is None:
                return tool_input

            from ..capabilities import ParameterResolver, get_default_registry

            spec = get_default_registry().get(cap_name)
            if spec is None or not spec.params:
                return tool_input

            resolver = ParameterResolver(spec, state)
            result = await resolver.resolve(provided=tool_input or {})

            if result.warnings:
                logger.debug("[Execute] resolve warnings for %s: %s", tool_name, result.warnings)

            if not result.ok:
                missing_names = [p.name for p in result.missing]
                logger.warning(
                    "[Execute] %s — 필수 파라미터 누락: %s (context에서 못 찾음)",
                    tool_name, missing_names,
                )
                # 누락 상태로도 실행은 시도 — 도구가 직접 에러 반환하게 함

            return result.args or tool_input
        except Exception as e:
            logger.debug("[Execute] capability enrich skipped for %s: %s", tool_name, e)
            return tool_input

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: PipelineState) -> str:
        """도구 실행 — ResourceRegistry / ToolSource / tool_registry(MCP 등) 순 디스패치"""
        return await asyncio.wait_for(
            self._dispatch_tool(tool_name, tool_input, state),
            timeout=self._tool_timeout,
        )

    async def _dispatch_tool(self, tool_name: str, tool_input: dict, state: PipelineState) -> str:
        """도구 디스패처 — ResourceRegistry → 플러그인 ToolSource → 레거시 폴백"""

        # 빌트인: discover_tools (progressive disclosure Level 2)
        if tool_name == "discover_tools":
            return self._handle_discover_tools(tool_input, state)

        # 빌트인: rag_search (에이전트가 직접 호출하는 RAG 검색)
        if tool_name == "rag_search":
            return await self._handle_rag_search(tool_input, state)

        # ResourceRegistry 경로 (XgenAdapter가 주입)
        registry = state.metadata.get("resource_registry")
        if registry:
            executors = registry.get_tool_executors()
            if tool_name in executors:
                return await registry.execute_tool(tool_name, tool_input)

        # 플러그인 ToolSource 경로 (register_tool_source로 등록된 소스)
        from ..tools import get_tool_sources
        for source in get_tool_sources():
            if source.has_tool(tool_name):
                result = await source.call_tool(tool_name, tool_input)
                if isinstance(result, dict):
                    return result.get("content", str(result))
                return str(result)

        # 레거시 폴백: state.metadata에 직접 등록된 Tool 인스턴스
        tool_registry = state.metadata.get("tool_registry", {})
        if tool_name in tool_registry:
            tool_instance = tool_registry[tool_name]
            if hasattr(tool_instance, 'execute'):
                result = await tool_instance.execute(tool_input)
                return result.content if hasattr(result, 'content') else str(result)

        # 미등록 도구
        return f"Error: Tool '{tool_name}' is not registered. Use discover_tools to see available tools."

    async def _handle_rag_search(self, tool_input: dict, state: PipelineState) -> str:
        """RAG 검색 도구 실행 — tool_registry에서 RAGSearchTool 인스턴스를 꺼내 실행"""
        tool_registry = state.metadata.get("tool_registry", {})
        rag_tool = tool_registry.get("rag_search")
        if rag_tool and hasattr(rag_tool, "execute"):
            result = await rag_tool.execute(tool_input)
            return result.content if hasattr(result, "content") else str(result)

        # 폴백: RAGSearchTool이 registry에 없으면 직접 생성 시도
        rag_collections = state.metadata.get("rag_collections", [])
        if not rag_collections:
            return "Error: No RAG collections configured. RAG search is not available."

        from ..tools.rag_tool import RAGSearchTool
        rag_top_k = state.metadata.get("rag_top_k", 4)
        fallback_tool = RAGSearchTool(collections=rag_collections, default_top_k=rag_top_k)
        result = await fallback_tool.execute(tool_input)
        return result.content if hasattr(result, "content") else str(result)

    def _handle_discover_tools(self, tool_input: dict, state: PipelineState) -> str:
        """Progressive Disclosure Level 2: 특정 도구의 상세 스키마 반환"""
        tool_name = tool_input.get("tool_name", "")

        if not tool_name:
            # 전체 목록
            lines = []
            for t in state.tool_index:
                lines.append(f"- {t['name']}: {t.get('description', '')}")
            return "\n".join(lines) if lines else "No tools available."

        # 특정 도구의 상세 스키마
        schema = state.tool_schemas.get(tool_name)
        if schema:
            import json
            return json.dumps(schema, indent=2, ensure_ascii=False)

        # tool_definitions에서 검색
        for td in state.tool_definitions:
            if td.get("name") == tool_name:
                import json
                return json.dumps(td, indent=2, ensure_ascii=False)

        return f"Tool '{tool_name}' not found in registry."

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "순차 실행 + 에러 허용", is_default=True),
            StrategyInfo("parallel_read", "읽기 도구 병렬, 쓰기 도구 직렬"),
        ]
