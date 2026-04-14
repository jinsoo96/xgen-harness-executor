"""
workflow_bridge — xgen-workflow harness_router.py 연동 모듈

기존 execute_via_harness()의 Rust subprocess 호출을
Python Pipeline 직접 호출로 교체하는 브릿지.

기존 로직을 100% 유지:
- 입력 추출 (_extract_input_from_workflow)
- RAG 선실행 (_pre_execute_rag_search)
- 파일 추출 (_extract_attached_files)
- 이전 결과 조회 (_fetch_previous_results)
- MCP 세션 수집 (_collect_mcp_session_ids)
- 에이전트 노드 설정 추출 (_extract_agent_config)
- API 키 해석 (환경변수 → xgen-core config)
- 이벤트 변환 (HarnessEvent → xgen SSE 포맷)

변경점:
- subprocess(JSON-RPC stdio) → Pipeline.run() + EventEmitter.stream()
"""

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Dict, Optional

from ..core.config import HarnessConfig
from ..core.pipeline import Pipeline
from ..core.state import PipelineState
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
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Python Pipeline으로 직접 실행.

    harness_router.py의 execute_via_harness()에서 호출.
    기존 입력 추출/RAG/파일/API키 로직은 harness_router.py가 그대로 처리하고,
    이 함수는 준비된 데이터로 파이프라인만 실행.
    """

    # 1. HarnessConfig 생성
    config_kwargs = {
        "preset": harness_pipeline,
        "provider": provider,
        "model": model,
        "temperature": temperature,
        "system_prompt": system_prompt,
    }
    if stages_list:
        # stages_list가 있으면 custom 프리셋으로 변환
        stage_map = {}
        for s in stages_list:
            # "input" → "s01_input", "llm" → "s07_llm" 등
            stage_id = _normalize_stage_id(s)
            if stage_id:
                stage_map[stage_id] = "default"
        if stage_map:
            config_kwargs["stages"] = stage_map
            config_kwargs["preset"] = "custom"

    config = HarnessConfig(**config_kwargs)

    # 2. EventEmitter 생성
    emitter = EventEmitter()

    # 3. Pipeline 빌드
    pipeline = Pipeline.from_config(config, emitter)

    # 4. PipelineState 생성
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

    # 5. 도구 URI → tool_definitions 변환 (향후 MCP 연동)
    if tools:
        state.metadata["tool_uris"] = tools
        # TODO: MCP client로 도구 스키마 가져와서 tool_definitions에 추가

    # 5.5. API 키를 환경변수에 임시 설정 (s01_input이 읽을 수 있도록)
    import os
    _KEY_MAP = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GEMINI_API_KEY",
    }
    env_key = _KEY_MAP.get(provider, f"{provider.upper()}_API_KEY")
    prev_value = os.environ.get(env_key, "")
    if api_key and not prev_value:
        os.environ[env_key] = api_key

    # 6. 백그라운드 파이프라인 실행
    async def _run():
        try:
            await pipeline.run(state)
        except Exception as e:
            logger.exception("[Bridge] Pipeline execution failed")
            await emitter.emit(ErrorEvent(message=str(e)))
            await emitter.emit(DoneEvent(final_output="", success=False))
        finally:
            # 환경변수 복원
            if not prev_value and env_key in os.environ:
                del os.environ[env_key]

    task = asyncio.create_task(_run())

    # 7. 이벤트 스트리밍 → xgen SSE 포맷 변환
    async for event in emitter.stream():
        converted = convert_to_xgen_event(event)
        if converted:
            yield converted

    # 파이프라인 완료 대기
    await task


# 스테이지 이름 정규화
_STAGE_ALIASES = {
    "input": "s01_input",
    "memory": "s02_memory",
    "system_prompt": "s03_system_prompt",
    "tool_index": "s04_tool_index",
    "plan": "s05_plan",
    "context": "s06_context",
    "llm": "s07_llm",
    "execute": "s08_execute",
    "validate": "s09_validate",
    "decide": "s10_decide",
    "save": "s11_save",
    "complete": "s12_complete",
}


def _normalize_stage_id(name: str) -> Optional[str]:
    """스테이지 이름을 정규화 (레거시 호환)"""
    name = name.strip().lower()
    if name.startswith("s") and "_" in name:
        return name  # 이미 정규화됨
    return _STAGE_ALIASES.get(name)
