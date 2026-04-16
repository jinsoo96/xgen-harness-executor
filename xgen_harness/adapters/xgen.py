"""
XgenAdapter — xgen-workflow ↔ 하네스 파이프라인 어댑터

xgen-workflow의 execution_core.py에서 이렇게만 쓰면 된다:

    adapter = XgenAdapter(db_manager=db_manager)
    async for event in adapter.execute(workflow_data, input_data, user_id=user_id):
        yield event  # 이미 xgen SSE 포맷

어댑터가 알아서 처리하는 것:
- workflow_data에서 harness_config, 에이전트 노드, MCP 세션, 파일 추출
- ServiceProvider 생성 (DB/Config/MCP/Documents)
- API 키 해석 (ServiceProvider → 환경변수 → 폴백)
- 파이프라인 실행 + 이벤트를 xgen SSE 포맷으로 변환

xgen-workflow가 몰라도 되는 것:
- 12스테이지 파이프라인 내부 구조
- Strategy/Artifact 시스템
- 이벤트 타입 (StageEnterEvent, MessageEvent 등)
"""

import json
import logging
import os
from typing import Any, AsyncGenerator, Dict, Optional

from ..core.config import HarnessConfig
from ..core.pipeline import Pipeline
from ..core.state import PipelineState
from ..core.services import ServiceProvider, NullServiceProvider
from ..events.emitter import EventEmitter
from ..events.types import DoneEvent, ErrorEvent
from ..integrations.xgen_streaming import convert_to_xgen_event
from ..providers import get_api_key_env

import asyncio

logger = logging.getLogger("harness.adapter.xgen")


class XgenAdapter:
    """xgen-workflow → 하네스 파이프라인 어댑터.

    xgen 환경의 모든 복잡성(워크플로우 데이터, 노드 구조, MCP 세션 등)을
    하네스 파이프라인이 이해하는 형태로 번역한다.

    Args:
        db_manager: xgen-workflow DatabaseClient (Optional)
        services: 직접 ServiceProvider 주입 (Optional, db_manager보다 우선)
    """

    def __init__(self, db_manager=None, services: Optional[ServiceProvider] = None):
        if services:
            self._services = services
        elif db_manager:
            try:
                from ..integrations.xgen_services import XgenServiceProvider
                self._services = XgenServiceProvider.create(db_manager=db_manager)
            except Exception as e:
                logger.warning("[Adapter] XgenServiceProvider 생성 실패: %s", e)
                self._services = NullServiceProvider()
        else:
            self._services = NullServiceProvider()

    async def execute(
        self,
        workflow_data: Dict[str, Any],
        input_data: Any,
        user_id: int = 0,
        interaction_id: str = "",
        workflow_id: str = "",
        workflow_name: str = "",
        attached_files: Optional[list] = None,
        runtime_harness_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """워크플로우 실행 → xgen SSE 이벤트 스트림.

        Args:
            workflow_data: 워크플로우 JSON (nodes, edges, harness_config 포함)
            input_data: 사용자 입력 (str, dict, list)
            user_id: 실행자 ID
            interaction_id: 인터랙션 ID
            workflow_id: 워크플로우 ID
            workflow_name: 워크플로우 이름
            attached_files: 첨부 파일 목록
            runtime_harness_config: 런타임 오버라이드 (프론트에서 전달)

        Yields:
            xgen SSE 포맷 dict:
            - {"type": "data", "content": "..."}
            - {"type": "log", "data": {...}}
            - {"type": "tool", "data": {...}}
            - {"type": "end", "message": "..."}
            - {"type": "error", "detail": "..."}
        """
        # ━━━━ 1. 입력 추출 ━━━━
        text = self._extract_text(input_data, workflow_data)
        if not text.strip():
            yield {"type": "error", "detail": "입력 텍스트가 비어있습니다."}
            return

        # ━━━━ 2. harness_config 해석 ━━━━
        hc = dict(workflow_data.get("harness_config") or {})
        if runtime_harness_config:
            hc.update(runtime_harness_config)

        agent_config = self._extract_agent_config(workflow_data)

        provider = hc.get("provider") or (agent_config or {}).get("provider", "anthropic")
        model = hc.get("model") or (agent_config or {}).get("model", "claude-sonnet-4-20250514")
        temperature = hc.get("temperature") if hc.get("temperature") is not None else 0.7
        system_prompt = hc.get("system_prompt") or (agent_config or {}).get("system_prompt", "")

        # ━━━━ 3. API 키 해석 — ServiceProvider 우선 ━━━━
        api_key = os.environ.get(get_api_key_env(provider), "")
        if not api_key and self._services.config:
            try:
                api_key = await self._services.config.get_api_key(provider) or ""
            except Exception as e:
                logger.warning("[Adapter] API 키 조회 실패: %s", e)

        if not api_key:
            yield {"type": "error", "detail": f"{provider} API 키가 설정되지 않았습니다."}
            return

        # ━━━━ 4. MCP 세션 수집 ━━━━
        mcp_sessions = self._collect_mcp_sessions(workflow_data, hc)

        # ━━━━ 5. 스테이지 설정 ━━━━
        stages_list = None
        hc_stages = hc.get("stages")
        if isinstance(hc_stages, list) and hc_stages:
            stages_list = hc_stages
        elif isinstance(hc_stages, dict):
            stages_list = list(hc_stages.keys())

        # ━━━━ 6. HarnessConfig 생성 ━━━━
        config = HarnessConfig(
            provider=provider,
            model=model,
            temperature=float(temperature),
            system_prompt=system_prompt,
        )

        # ━━━━ 7. Pipeline + State 생성 ━━━━
        emitter = EventEmitter()
        pipeline = Pipeline.from_config(config, emitter)

        state = PipelineState(
            user_input=text,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            interaction_id=interaction_id,
            user_id=str(user_id),
            attached_files=attached_files or [],
            workflow_data=workflow_data,
        )
        state.metadata["services"] = self._services

        # ━━━━ 8. API 키 환경변수 설정 (프로바이더가 읽을 수 있도록) ━━━━
        env_key = get_api_key_env(provider)
        prev_env = os.environ.get(env_key, "")
        os.environ[env_key] = api_key

        try:
            # ━━━━ 9. MCP 도구 디스커버리 ━━━━
            if mcp_sessions and self._services.mcp:
                await self._discover_mcp_tools(mcp_sessions, state)

            # ━━━━ 10. 파이프라인 실행 ━━━━
            logger.info(
                "[Adapter] 실행: provider=%s, model=%s, mcp=%d, tools=%d",
                provider, model, len(mcp_sessions), len(state.tool_definitions),
            )

            full_response = []

            async def _run():
                try:
                    await pipeline.run(state)
                except Exception as e:
                    logger.exception("[Adapter] Pipeline failed")
                    await emitter.emit(ErrorEvent(message=str(e)))
                    await emitter.emit(DoneEvent(final_output="", success=False))

            task = asyncio.create_task(_run())

            async for event in emitter.stream():
                xgen_event = convert_to_xgen_event(event)
                if not xgen_event:
                    continue

                evt_type = xgen_event.get("type")
                if evt_type == "data":
                    data = xgen_event.get("data", {})
                    content = data.get("content", "") if isinstance(data, dict) else str(data)
                    if content:
                        full_response.append(content)
                        yield {"type": "data", "content": content}
                elif evt_type == "log":
                    yield {"type": "log", "data": xgen_event.get("data", {})}
                elif evt_type == "tool":
                    yield {"type": "tool", "data": xgen_event.get("data", {})}
                elif evt_type == "error":
                    err = xgen_event.get("data", {})
                    detail = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    yield {"type": "error", "detail": detail}
                elif evt_type == "end":
                    end_data = xgen_event.get("data", {})
                    final = end_data.get("final_output", "")
                    if final and not full_response:
                        full_response.append(final)
                        yield {"type": "data", "content": final}

            await task
            yield {"type": "end", "message": "Stream finished"}

            logger.info("[Adapter] 완료: %d자 출력", sum(len(c) for c in full_response))

        finally:
            if prev_env:
                os.environ[env_key] = prev_env
            elif env_key in os.environ:
                del os.environ[env_key]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  내부 헬퍼 — 워크플로우 데이터 파싱
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _extract_text(self, input_data: Any, workflow_data: dict) -> str:
        """사용자 입력 추출."""
        if isinstance(input_data, dict):
            text = input_data.get("text", json.dumps(input_data, ensure_ascii=False))
        elif isinstance(input_data, list):
            text = json.dumps(input_data, ensure_ascii=False)
        else:
            text = str(input_data) if input_data else ""

        if not text.strip():
            for node in workflow_data.get("nodes", []):
                if node.get("data", {}).get("functionId") == "startnode":
                    params = node.get("data", {}).get("parameters", [])
                    if params and params[0].get("value"):
                        text = str(params[0]["value"])
                        break
        return text

    def _extract_agent_config(self, workflow_data: dict) -> Optional[dict]:
        """에이전트 노드에서 설정 추출."""
        for node in workflow_data.get("nodes", []):
            nd = node.get("data", {})
            if nd.get("functionId") == "agents" and nd.get("categoryId") == "xgen":
                params_dict = {}
                for p in nd.get("parameters", []) or []:
                    if p.get("value") is not None:
                        params_dict[p["id"]] = p["value"]
                if params_dict:
                    return params_dict
        return None

    def _collect_mcp_sessions(self, workflow_data: dict, hc: dict) -> list[str]:
        """워크플로우 + harness_config에서 MCP 세션 수집."""
        sessions = []
        for node in workflow_data.get("nodes", []):
            nd = node.get("data", {})
            for p in nd.get("parameters", []) or []:
                if p.get("id") in ("mcp_session_id", "session_id") and p.get("value"):
                    sid = str(p["value"]).strip()
                    if sid and sid not in sessions:
                        sessions.append(sid)

        for sid in hc.get("mcp_sessions", []):
            if sid and sid not in sessions:
                sessions.append(sid)

        return sessions

    async def _discover_mcp_tools(self, session_ids: list[str], state: PipelineState) -> None:
        """ServiceProvider.mcp를 통한 도구 디스커버리."""
        mcp = self._services.mcp
        if not mcp:
            return

        tool_sessions = {}
        for sid in session_ids:
            try:
                tools = await mcp.list_tools(sid)
                for tool in tools:
                    name = tool.get("name", "")
                    if not name:
                        continue
                    state.tool_definitions.append({
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": tool.get("description", ""),
                            "input_schema": tool.get("inputSchema", tool.get("input_schema", {})),
                        },
                    })
                    tool_sessions[name] = sid
                logger.info("[Adapter] MCP %s: %d tools", sid, len(tools))
            except Exception as e:
                logger.warning("[Adapter] MCP %s failed: %s", sid, e)

        if tool_sessions:
            state.metadata["mcp_tool_sessions"] = tool_sessions
