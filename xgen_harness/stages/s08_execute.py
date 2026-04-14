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
        results: list[dict[str, Any]] = []
        total_chars = 0

        for tc in tool_calls:
            tool_use_id = tc.get("tool_use_id", "")
            tool_name = tc.get("tool_name", "")
            tool_input = tc.get("tool_input", {})

            try:
                result_text = await self._execute_tool(tool_name, tool_input, state)

                # 결과 축약 (예산 초과 시)
                if total_chars + len(result_text) > result_budget:
                    remaining = max(0, result_budget - total_chars)
                    result_text = result_text[:remaining] + f"\n... (축약됨, 원본 {len(result_text)}자)"

                total_chars += len(result_text)

                state.add_tool_result(tool_use_id, result_text, is_error=False)
                results.append({"tool_name": tool_name, "success": True, "chars": len(result_text)})

                if state.event_emitter:
                    await state.event_emitter.emit(ToolResultEvent(
                        tool_use_id=tool_use_id,
                        tool_name=tool_name,
                        result=result_text[:500],  # 이벤트에는 500자까지만
                        is_error=False,
                    ))

            except asyncio.TimeoutError:
                error_msg = f"Tool '{tool_name}' timed out after {tool_timeout}s"
                state.add_tool_result(tool_use_id, error_msg, is_error=True)
                results.append({"tool_name": tool_name, "success": False, "error": "timeout"})
                logger.warning("[Execute] %s", error_msg)

            except Exception as e:
                error_msg = f"Tool '{tool_name}' failed: {str(e)}"
                state.add_tool_result(tool_use_id, error_msg, is_error=True)
                results.append({"tool_name": tool_name, "success": False, "error": str(e)})
                logger.error("[Execute] %s\n%s", error_msg, traceback.format_exc())

                if state.event_emitter:
                    await state.event_emitter.emit(ToolResultEvent(
                        tool_use_id=tool_use_id,
                        tool_name=tool_name,
                        result=error_msg,
                        is_error=True,
                    ))

        # 도구 결과를 messages에 flush
        state.flush_tool_results()
        state.tools_executed_count += len(results)

        executed_count = sum(1 for r in results if r["success"])
        error_count = sum(1 for r in results if not r["success"])

        logger.info("[Execute] %d tools executed, %d errors", executed_count, error_count)
        return {
            "tools_executed": len(results),
            "success_count": executed_count,
            "error_count": error_count,
            "total_chars": total_chars,
        }

    async def _execute_tool(self, tool_name: str, tool_input: dict, state: PipelineState) -> str:
        """도구 실행 — 현재는 도구 레지스트리에서 찾아서 실행"""
        # TODO: MCP 도구, 노드 브릿지 도구 등 통합
        # 현재는 state.tool_definitions에서 이름으로 매칭
        # 실제 실행은 MCP client나 builtin 도구로 위임

        # placeholder: 도구 이름 기반 라우팅
        return await asyncio.wait_for(
            self._dispatch_tool(tool_name, tool_input, state),
            timeout=self._tool_timeout,
        )

    async def _dispatch_tool(self, tool_name: str, tool_input: dict, state: PipelineState) -> str:
        """도구 디스패처 — discover_tools 빌트인 + MCP + 노드 브릿지"""

        # 빌트인: discover_tools (progressive disclosure Level 2)
        if tool_name == "discover_tools":
            return self._handle_discover_tools(tool_input, state)

        # state.metadata에 등록된 Tool 인스턴스 검색
        tool_registry = state.metadata.get("tool_registry", {})
        if tool_name in tool_registry:
            tool_instance = tool_registry[tool_name]
            result = await tool_instance.execute(tool_input)
            return result.content

        # MCP 도구 — mcp_tools 매핑에서 검색
        mcp_mapping = state.metadata.get("mcp_tool_mapping", {})
        if tool_name in mcp_mapping:
            from ..tools.mcp_client import MCPClient
            session_id = mcp_mapping[tool_name]
            client = MCPClient()
            return await client.call_tool(session_id, tool_name, tool_input)

        # 커스텀 API 도구 — stage_params.custom_tools에서 선택된 도구
        custom_tools = state.metadata.get("custom_tool_specs", {})
        if tool_name in custom_tools:
            return await self._execute_custom_api_tool(custom_tools[tool_name], tool_input)

        # 미등록 도구
        return f"Error: Tool '{tool_name}' is not registered. Use discover_tools to see available tools."

    async def _execute_custom_api_tool(self, tool_spec: dict, tool_input: dict) -> str:
        """xgen tools storage에서 등록된 커스텀 API 도구 실행"""
        import httpx

        api_url = tool_spec.get("api_url", "")
        api_method = tool_spec.get("api_method", "POST").upper()
        api_body = tool_spec.get("api_body", {})

        # tool_input으로 body 오버라이드
        body = {**api_body, **tool_input}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
                if api_method == "GET":
                    resp = await client.get(api_url, params=body)
                else:
                    resp = await client.post(api_url, json=body)

                if resp.status_code == 200:
                    return resp.text[:10000]
                else:
                    return f"API error: {resp.status_code} {resp.text[:500]}"
        except Exception as e:
            return f"API call failed: {str(e)}"

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
