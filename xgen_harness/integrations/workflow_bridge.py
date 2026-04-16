"""
workflow_bridge — xgen-workflow 연동 브릿지

execution_core.py에서 호출. 준비된 데이터로 파이프라인 실행.
ServiceProvider를 통해 xgen 서비스(DB/Config/MCP/Documents)에 접근.
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from ..core.config import HarnessConfig
from ..core.execution_context import set_execution_context
from ..core.pipeline import Pipeline
from ..core.state import PipelineState
from ..core.services import ServiceProvider, NullServiceProvider
from ..events.emitter import EventEmitter
from ..events.types import DoneEvent, ErrorEvent
from ..integrations.xgen_streaming import convert_to_xgen_event

logger = logging.getLogger("harness.bridge")


async def execute_via_python_pipeline(
    workflow_data: dict,
    text: str,
    user_id: str,
    provider: str,
    model: str,
    api_key: str,
    temperature: float = 0.7,
    system_prompt: str = "",
    harness_pipeline: str = "standard",
    stages_list: Optional[list] = None,
    tools: Optional[list] = None,
    workflow_id: str = "",
    workflow_name: str = "",
    interaction_id: str = "",
    attached_files: Optional[list] = None,
    previous_results: Optional[list] = None,
    rag_context: str = "",
    mcp_sessions: Optional[list] = None,
    db_manager=None,
    services: Optional[ServiceProvider] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Python Pipeline으로 직접 실행.

    Args:
        services: ServiceProvider 인스턴스. 없으면 NullServiceProvider 사용.
        db_manager: (레거시 호환) services가 없을 때 DB 서비스용.
        나머지: 워크플로우/LLM 설정.
    """

    # ServiceProvider 확정
    if services is None:
        if db_manager is not None:
            # 레거시 호환: db_manager만 전달된 경우 XgenServiceProvider 생성 시도
            try:
                from ..integrations.xgen_services import XgenServiceProvider
                services = XgenServiceProvider.create(db_manager=db_manager)
            except Exception as e:
                logger.warning("[Bridge] XgenServiceProvider 생성 실패, NullServiceProvider 사용: %s", e)
                services = NullServiceProvider()
        else:
            services = NullServiceProvider()

    # 1. API 키 해석 — ServiceProvider 우선, 폴백으로 직접 전달값 사용
    resolved_api_key = api_key
    if not resolved_api_key and services.config:
        try:
            resolved_api_key = await services.config.get_api_key(provider) or ""
        except Exception as e:
            logger.warning("[Bridge] API 키 조회 실패: %s", e)

    if not resolved_api_key:
        yield {"type": "error", "data": {"message": "API 키를 찾을 수 없습니다."}}
        return

    # 2. API 키를 실행 컨텍스트에 설정 (contextvars — 동시 실행 격리)
    set_execution_context(api_key=resolved_api_key, provider=provider, model=model)

    try:
        # 3. HarnessConfig 생성
        config_kwargs = {
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "system_prompt": system_prompt,
        }
        if stages_list:
            stage_map = {}
            for s in stages_list:
                stage_id = _normalize_stage_id(s)
                if stage_id:
                    stage_map[stage_id] = "default"
            if stage_map:
                config_kwargs["stages"] = stage_map

        config = HarnessConfig(**config_kwargs)

        # 4. EventEmitter + Pipeline
        emitter = EventEmitter()
        pipeline = Pipeline.from_config(config, emitter)

        # 5. PipelineState
        state = PipelineState(
            user_input=text,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            interaction_id=interaction_id,
            user_id=user_id,
            attached_files=attached_files or [],
            previous_results=previous_results or [],
            rag_context=rag_context,
            workflow_data=workflow_data,
        )

        # ServiceProvider를 state에 주입
        state.metadata["services"] = services

        # 6. MCP 도구 디스커버리 (ServiceProvider 활용)
        if mcp_sessions and services.mcp:
            for sid in mcp_sessions:
                try:
                    tools_data = await services.mcp.list_tools(sid)
                    for tool in tools_data:
                        tool_def = {
                            "name": tool.get("name", ""),
                            "description": tool.get("description", ""),
                            "input_schema": tool.get("inputSchema", tool.get("input_schema", {})),
                        }
                        if tool_def["name"]:
                            state.tool_definitions.append({"type": "function", "function": tool_def})
                            # MCP 라우팅 정보 저장
                            if "mcp_tool_sessions" not in state.metadata:
                                state.metadata["mcp_tool_sessions"] = {}
                            state.metadata["mcp_tool_sessions"][tool_def["name"]] = sid
                    logger.info("[Bridge] MCP session %s: %d tools loaded", sid, len(tools_data))
                except Exception as e:
                    logger.warning("[Bridge] MCP session %s tool discovery failed: %s", sid, e)
        elif mcp_sessions:
            # ServiceProvider 없으면 URI만 기록
            state.metadata["tool_uris"] = [f"mcp://session/{sid}" for sid in mcp_sessions]

        # 7. 백그라운드 파이프라인 실행
        async def _run():
            try:
                await pipeline.run(state)
            except Exception as e:
                logger.exception("[Bridge] Pipeline execution failed")
                await emitter.emit(ErrorEvent(message=str(e)))
                await emitter.emit(DoneEvent(final_output="", success=False))

        task = asyncio.create_task(_run())

        # 8. 이벤트 스트리밍 → xgen SSE 포맷 변환
        async for event in emitter.stream():
            converted = convert_to_xgen_event(event)
            if converted:
                yield converted

        await task

    finally:
        pass  # contextvars는 자동 격리 — 복원 불필요


# 스테이지 이름 정규화
_STAGE_ALIASES = {
    "input": "s01_input", "memory": "s02_memory",
    "system_prompt": "s03_system_prompt", "tool_index": "s04_tool_index",
    "plan": "s05_plan", "context": "s06_context",
    "llm": "s07_llm", "execute": "s08_execute",
    "validate": "s09_validate", "decide": "s10_decide",
    "save": "s11_save", "complete": "s12_complete",
}


def _normalize_stage_id(name: str) -> Optional[str]:
    name = name.strip().lower()
    if name.startswith("s") and "_" in name:
        return name
    return _STAGE_ALIASES.get(name)
