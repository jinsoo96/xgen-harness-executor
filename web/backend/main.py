"""
xgen-harness-web 백엔드

캔버스 대체 UI용 API. 12개 스테이지 개별 토글, 실시간 실행.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from xgen_harness import (
    Pipeline, PipelineState, HarnessConfig, ALL_STAGES, REQUIRED_STAGES,
    ArtifactRegistry, EventEmitter,
)
from xgen_harness.events.types import (
    HarnessEvent, DoneEvent, ErrorEvent, MessageEvent,
    StageEnterEvent, StageExitEvent, MetricsEvent,
    ToolCallEvent, ToolResultEvent, EvaluationEvent,
    event_to_dict,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("harness-web")

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")


# === 세션 ===
class Session:
    def __init__(self, sid: str):
        self.id = sid
        self.disabled_stages: set[str] = set()
        self.artifacts: dict[str, str] = {}
        self.provider: str = "anthropic"
        self.model: str = "claude-sonnet-4-20250514"
        self.created_at: str = ""

sessions: dict[str, Session] = {}


# === App ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("xgen-harness-web starting")
    yield

app = FastAPI(title="xgen-harness-web", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# === API ===

@app.get("/health")
async def health():
    return {"status": "ok", "api_key_configured": bool(API_KEY or OPENAI_KEY), "sessions": len(sessions)}

@app.get("/api/config")
async def get_config():
    return {"apiKeyConfigured": bool(API_KEY or OPENAI_KEY)}


# --- 스테이지 ---

@app.get("/api/pipeline/describe")
async def describe_pipeline(preset: str = Query("")):
    """12개 스테이지 전체 반환. 프리셋 무시, 전부 active=True."""
    registry = ArtifactRegistry.default()
    config = HarnessConfig()
    stages = registry.describe_all(config)
    # 전부 active
    for s in stages:
        s["active"] = True
    return {"stages": stages, "total": len(stages)}

@app.get("/api/stages")
async def list_stages():
    """스테이지 목록 + 필수 여부 + 설정 스키마 (describe_all에서 config 포함)"""
    registry = ArtifactRegistry.default()
    stages = registry.describe_all()
    return {"stages": stages, "required": list(REQUIRED_STAGES)}


# --- 세션 ---

class CreateSessionRequest(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"

@app.post("/api/sessions")
async def create_session(req: CreateSessionRequest):
    sid = str(uuid.uuid4())[:8]
    s = Session(sid)
    s.provider = req.provider
    s.model = req.model
    from datetime import datetime, timezone
    s.created_at = datetime.now(timezone.utc).isoformat()
    sessions[sid] = s
    return {"id": sid, "provider": s.provider, "model": s.model, "created_at": s.created_at}

@app.get("/api/sessions")
async def list_sessions():
    return [{"id": s.id, "provider": s.provider, "model": s.model, "created_at": s.created_at} for s in sessions.values()]

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    sessions.pop(session_id, None)
    return {"ok": True}


# --- 스테이지 토글 (세션별) ---

class ToggleRequest(BaseModel):
    stage_id: str
    active: bool

@app.post("/api/sessions/{session_id}/toggle")
async def toggle_stage(session_id: str, req: ToggleRequest):
    s = sessions.get(session_id)
    if not s:
        return {"error": "session not found"}
    if req.stage_id in REQUIRED_STAGES and not req.active:
        return {"error": "required stage", "stage_id": req.stage_id}
    if req.active:
        s.disabled_stages.discard(req.stage_id)
    else:
        s.disabled_stages.add(req.stage_id)
    return {"ok": True, "disabled_stages": list(s.disabled_stages)}


# --- API 키 ---

class SetApiKeyRequest(BaseModel):
    api_key: str
    provider: str = "anthropic"

@app.post("/api/config/api-key")
async def set_api_key(req: SetApiKeyRequest):
    global API_KEY, OPENAI_KEY
    if req.provider == "anthropic":
        API_KEY = req.api_key
        os.environ["ANTHROPIC_API_KEY"] = req.api_key
    elif req.provider == "openai":
        OPENAI_KEY = req.api_key
        os.environ["OPENAI_API_KEY"] = req.api_key
    return {"ok": True}


# === WebSocket 실행 ===

@app.websocket("/ws/execute/{session_id}")
async def ws_execute(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = sessions.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "data": {"message": "Session not found"}})
        await websocket.close()
        return

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "execute":
                continue

            user_input = data.get("input", "")
            api_key = data.get("api_key", "") or API_KEY
            provider = data.get("provider", "") or session.provider
            model = data.get("model", "") or session.model

            if not api_key and not OPENAI_KEY:
                await websocket.send_json({"type": "error", "data": {"message": "API key not configured"}})
                continue

            if not api_key and OPENAI_KEY:
                api_key = OPENAI_KEY
                provider = "openai"
                model = "gpt-4o-mini"

            _KEY_MAP = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
            env_key = _KEY_MAP.get(provider, f"{provider.upper()}_API_KEY")
            os.environ[env_key] = api_key

            # disabled_stages / stage_params를 실행 메시지에서도 받음
            runtime_disabled = data.get("disabled_stages", [])
            runtime_stage_params = data.get("stage_params", {})

            disabled = session.disabled_stages.copy()
            if runtime_disabled:
                disabled.update(set(runtime_disabled) - REQUIRED_STAGES)

            config = HarnessConfig(
                provider=provider,
                model=model,
                disabled_stages=disabled,
                artifacts=session.artifacts.copy(),
                stage_params=runtime_stage_params,
            )

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


def _ws_event(event: HarnessEvent) -> dict:
    if isinstance(event, StageEnterEvent):
        return {"type": "stage.enter", "stage": event.stage_name, "stage_id": event.stage_id, "iteration": 0, "timestamp": event.timestamp,
                "data": {"phase": event.phase, "step": event.step, "total": event.total, "bypassed": event.description == "bypassed"}}
    if isinstance(event, StageExitEvent):
        return {"type": "stage.exit", "stage": event.stage_name, "stage_id": event.stage_id, "iteration": 0, "timestamp": event.timestamp, "data": event.output or {}}
    if isinstance(event, MessageEvent):
        return {"type": "text.delta", "stage": "LLM", "iteration": 0, "timestamp": event.timestamp, "data": {"text": event.text}}
    if isinstance(event, ToolCallEvent):
        return {"type": "tool.call", "stage": "Execute", "iteration": 0, "timestamp": event.timestamp, "data": {"tool_name": event.tool_name, "tool_input": event.tool_input}}
    if isinstance(event, ToolResultEvent):
        return {"type": "tool.result", "stage": "Execute", "iteration": 0, "timestamp": event.timestamp, "data": {"tool_name": event.tool_name, "result": event.result[:500], "is_error": event.is_error}}
    if isinstance(event, EvaluationEvent):
        return {"type": "evaluation", "stage": "Validate", "iteration": 0, "timestamp": event.timestamp, "data": {"score": event.score, "feedback": event.feedback, "verdict": event.verdict}}
    if isinstance(event, MetricsEvent):
        return {"type": "pipeline.metrics", "stage": "", "iteration": 0, "timestamp": event.timestamp,
                "data": {"duration_ms": event.duration_ms, "total_tokens": event.total_tokens, "input_tokens": event.input_tokens, "output_tokens": event.output_tokens, "cost_usd": event.cost_usd, "llm_calls": event.llm_calls, "model": event.model}}
    if isinstance(event, DoneEvent):
        return {"type": "pipeline.complete", "stage": "", "iteration": 0, "timestamp": event.timestamp, "data": {"success": event.success, "text": event.final_output}}
    if isinstance(event, ErrorEvent):
        return {"type": "pipeline.error", "stage": event.stage_id, "iteration": 0, "timestamp": event.timestamp, "data": {"message": event.message}}
    return event_to_dict(event)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8088)
