"""
하네스 API 라우터 — xgen-workflow에 include

xgen-workflow main.py:
    from xgen_harness.api.router import harness_router
    app.include_router(harness_router, prefix="/api/harness")

엔드포인트:
    GET  /api/harness/stages      — 12개 스테이지 설명 + required 정보
    POST /api/harness/execute     — SSE 스트리밍 실행
    POST /api/harness/orchestrate — 멀티에이전트 DAG SSE 실행
    WS   /api/harness/ws/{sid}    — WebSocket 실행
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Optional

from ..core.config import HarnessConfig, ALL_STAGES, REQUIRED_STAGES
from ..core.registry import ArtifactRegistry

logger = logging.getLogger("harness.api")

try:
    from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel

    harness_router = APIRouter(tags=["harness"])

    # === Models ===

    class ExecuteRequest(BaseModel):
        text: str
        provider: str = "anthropic"
        # model 미지정 시 provider 의 PROVIDER_DEFAULT_MODEL 조회 (빈 문자열이면 adapter/stage 에서 해석).
        model: str = ""
        temperature: float = 0.7
        system_prompt: str = ""
        disabled_stages: list[str] = []
        artifacts: dict[str, str] = {}

    # === 스테이지 ===

    @harness_router.get("/stages")
    async def list_stages():
        """12개 스테이지 전체 + required + 설정 스키마"""
        from ..core.stage_config import get_all_stage_configs
        registry = ArtifactRegistry.default()
        stages = registry.describe_all()
        configs = get_all_stage_configs()
        for s in stages:
            s["active"] = True
            s["required"] = s["stage_id"] in REQUIRED_STAGES
            cfg = configs.get(s["stage_id"], {})
            s["icon"] = cfg.get("icon", "")
            s["description_ko"] = cfg.get("description_ko", "")
            s["description_en"] = cfg.get("description_en", "")
            s["fields"] = cfg.get("fields", [])
            s["behavior"] = cfg.get("behavior", [])
        return {"stages": stages, "required": list(REQUIRED_STAGES), "all": ALL_STAGES}

    # === 동적 옵션 (MCP 세션, RAG 컬렉션) ===

    @harness_router.get("/options/mcp-sessions")
    async def list_mcp_sessions():
        """사용 가능한 MCP 세션 목록 (UI multi_select 옵션 제공)"""
        import httpx as _httpx
        from ..core.service_registry import get_service_url
        mcp_url = get_service_url("mcp")
        try:
            async with _httpx.AsyncClient(timeout=_httpx.Timeout(5)) as client:
                resp = await client.get(f"{mcp_url}/api/mcp/sessions")
                if resp.status_code == 200:
                    data = resp.json()
                    # 응답이 list거나 dict의 sessions 키 안에 있을 수 있음
                    sessions = data if isinstance(data, list) else data.get("sessions", [])
                    if isinstance(sessions, list):
                        result = []
                        for s in sessions:
                            if isinstance(s, dict):
                                sid = s.get("session_id", s.get("id", ""))
                                name = s.get("session_name", s.get("name", sid))
                                if sid:
                                    result.append({"id": sid, "name": name})
                        logger.info("[API] MCP sessions: %d found", len(result))
                        return {"sessions": result}
        except Exception as e:
            logger.warning("[API] MCP sessions fetch failed: %s", e)
        return {"sessions": []}

    @harness_router.get("/options/rag-collections")
    async def list_rag_collections():
        """사용 가능한 RAG 문서 컬렉션 (UI multi_select 옵션 제공)"""
        import httpx as _httpx
        from ..core.service_registry import get_service_url as _get_url
        docs_url = _get_url("xgen-documents")
        try:
            async with _httpx.AsyncClient(timeout=_httpx.Timeout(5)) as client:
                resp = await client.get(
                    f"{docs_url}/api/retrieval/documents/collections",
                    headers={"x-user-admin": "true", "x-user-superuser": "true"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # 응답 구조: {"collections": [...]} 또는 직접 list
                    cols = data.get("collections", data) if isinstance(data, dict) else data
                    if isinstance(cols, list):
                        result = []
                        for c in cols:
                            if isinstance(c, dict):
                                name = c.get("name", c.get("collection_name", ""))
                                desc = c.get("description", "")
                                if name:
                                    entry = {"name": name}
                                    if desc:
                                        entry["description"] = desc
                                    result.append(entry)
                            elif isinstance(c, str) and c:
                                result.append({"name": c})
                        logger.info("[API] RAG collections: %d found", len(result))
                        return {"collections": result}
        except Exception as e:
            logger.warning("[API] RAG collections fetch failed: %s", e)
        return {"collections": []}

    # === SSE 실행 ===

    @harness_router.post("/execute")
    async def execute_pipeline(req: ExecuteRequest):
        from ..core.pipeline import Pipeline
        from ..core.state import PipelineState
        from ..events.emitter import EventEmitter
        from ..events.types import DoneEvent, ErrorEvent, event_to_dict

        config = HarnessConfig(
            provider=req.provider, model=req.model,
            temperature=req.temperature, system_prompt=req.system_prompt,
            disabled_stages=set(req.disabled_stages),
            artifacts=req.artifacts,
        )
        emitter = EventEmitter()
        pipeline = Pipeline.from_config(config, emitter)
        state = PipelineState(user_input=req.text)

        async def gen():
            task = asyncio.create_task(pipeline.run(state))
            async for event in emitter.stream():
                yield f"data: {json.dumps(event_to_dict(event), ensure_ascii=False)}\n\n"
            await task

        return StreamingResponse(gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    # === WebSocket 실행 ===

    @harness_router.websocket("/ws/{session_id}")
    async def ws_execute(websocket: WebSocket, session_id: str):
        from ..core.pipeline import Pipeline
        from ..core.state import PipelineState
        from ..events.emitter import EventEmitter
        from ..events.types import (
            StageEnterEvent, StageExitEvent, MessageEvent,
            ToolCallEvent, ToolResultEvent, EvaluationEvent,
            MetricsEvent, ErrorEvent, DoneEvent, event_to_dict,
        )

        await websocket.accept()

        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") != "execute":
                    continue

                from ..providers import PROVIDER_DEFAULT_MODEL
                user_input = data.get("input", "")
                provider = data.get("provider", "anthropic")
                model = data.get("model") or PROVIDER_DEFAULT_MODEL.get(provider, "")
                disabled = set(data.get("disabled_stages", []))

                config = HarnessConfig(provider=provider, model=model, disabled_stages=disabled)
                emitter = EventEmitter()
                pipeline = Pipeline.from_config(config, emitter)
                state = PipelineState(user_input=user_input)

                async def run():
                    try:
                        await pipeline.run(state)
                    except Exception as e:
                        await emitter.emit(ErrorEvent(message=str(e)))
                        await emitter.emit(DoneEvent(final_output="", success=False))

                task = asyncio.create_task(run())

                async for event in emitter.stream():
                    try:
                        await websocket.send_json(_ws_event(event))
                    except Exception:
                        break

                await task

        except WebSocketDisconnect:
            pass

    # === 오케스트레이터 ===

    class OrchestratorRequest(BaseModel):
        text: str
        workflow_data: dict
        provider: str = "anthropic"
        # sentinel "" — 비어있으면 MultiAgentExecutor 가 PROVIDER_DEFAULT_MODEL 에서 해석
        model: str = ""

    @harness_router.post("/orchestrate")
    async def orchestrate(req: OrchestratorRequest):
        from ..orchestrator.multi_agent import MultiAgentExecutor
        from ..events.emitter import EventEmitter
        from ..events.types import event_to_dict

        emitter = EventEmitter()
        executor = MultiAgentExecutor(
            workflow_data=req.workflow_data, event_emitter=emitter,
            default_provider=req.provider, default_model=req.model,
        )

        async def gen():
            task = asyncio.create_task(executor.run(req.text))
            async for event in emitter.stream():
                yield f"data: {json.dumps(event_to_dict(event), ensure_ascii=False)}\n\n"
            await task

        return StreamingResponse(gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})

    # WS event converter
    def _ws_event(event) -> dict:
        from ..events.types import (
            StageEnterEvent, StageExitEvent, MessageEvent,
            ToolCallEvent, ToolResultEvent, EvaluationEvent,
            MetricsEvent, ErrorEvent, DoneEvent, event_to_dict,
        )
        if isinstance(event, StageEnterEvent):
            return {"type": "stage.enter", "stage": event.stage_name, "stage_id": event.stage_id, "timestamp": event.timestamp,
                    "data": {"phase": event.phase, "step": event.step, "total": event.total, "bypassed": event.description == "bypassed"}}
        if isinstance(event, StageExitEvent):
            return {"type": "stage.exit", "stage": event.stage_name, "stage_id": event.stage_id, "timestamp": event.timestamp, "data": event.output or {}}
        if isinstance(event, MessageEvent):
            return {"type": "text.delta", "stage": "LLM", "timestamp": event.timestamp, "data": {"text": event.text}}
        if isinstance(event, ToolCallEvent):
            return {"type": "tool.call", "stage": "Execute", "timestamp": event.timestamp, "data": {"tool_name": event.tool_name, "tool_input": event.tool_input}}
        if isinstance(event, ToolResultEvent):
            return {"type": "tool.result", "stage": "Execute", "timestamp": event.timestamp, "data": {"tool_name": event.tool_name, "result": event.result[:500]}}
        if isinstance(event, EvaluationEvent):
            return {"type": "evaluation", "stage": "Validate", "timestamp": event.timestamp, "data": {"score": event.score, "verdict": event.verdict}}
        if isinstance(event, MetricsEvent):
            return {"type": "pipeline.metrics", "timestamp": event.timestamp, "data": {"duration_ms": event.duration_ms, "total_tokens": event.total_tokens, "cost_usd": event.cost_usd, "model": event.model}}
        if isinstance(event, DoneEvent):
            return {"type": "pipeline.complete", "timestamp": event.timestamp, "data": {"success": event.success, "text": event.final_output}}
        if isinstance(event, ErrorEvent):
            return {"type": "pipeline.error", "timestamp": event.timestamp, "data": {"message": event.message}}
        return event_to_dict(event)

except ImportError:
    harness_router = None
