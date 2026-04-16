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
from ..core.execution_context import set_execution_context, get_api_key as ctx_get_api_key
from ..core.pipeline import Pipeline
from ..core.state import PipelineState
from ..core.services import ServiceProvider, NullServiceProvider
from ..core.service_registry import register_service, register_env_mapping
from ..events.emitter import EventEmitter
from ..events.types import DoneEvent, ErrorEvent
from ..integrations.xgen_streaming import convert_to_xgen_event
from ..providers import get_api_key_env
from .resource_registry import ResourceRegistry

import asyncio

logger = logging.getLogger("harness.adapter.xgen")


class XgenAdapter:
    """xgen-workflow → 하네스 파이프라인 어댑터.

    xgen 환경의 모든 복잡성(워크플로우 데이터, 노드 구조, MCP 세션 등)을
    하네스 파이프라인이 이해하는 형태로 번역한다.

    Args:
        db_manager: xgen-workflow DatabaseClient (Optional)
        services: 직접 ServiceProvider 주입 (Optional, db_manager보다 우선)
        llm_factory: xgen LLM 생성 함수 (Optional). (provider, model, api_key, **kwargs) → LangChain BaseChatModel.
                     지정하면 하네스 내장 프로바이더 대신 xgen의 LLM을 사용.
    """

    def __init__(self, db_manager=None, services: Optional[ServiceProvider] = None, llm_factory=None):
        # xgen 환경 서비스 엔드포인트 등록 — 하네스 라이브러리에 xgen 인프라를 끼워넣는 곳
        self._register_xgen_services()
        self._llm_factory = llm_factory
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
            # db_manager 없어도 xgen 환경이면 Config/MCP/Documents 서비스 사용 가능
            try:
                from ..integrations.xgen_services import XgenServiceProvider
                self._services = XgenServiceProvider.create()
            except Exception:
                self._services = NullServiceProvider()

    @staticmethod
    def _register_xgen_services():
        """xgen 환경의 서비스 엔드포인트를 하네스 ServiceRegistry에 등록.

        하네스 라이브러리는 인프라를 모른다. 이 메서드가 xgen 인프라를 끼워넣는 유일한 지점.
        다른 실행기(예: AWS, GCP)는 자기 환경에 맞는 등록을 하면 된다.
        """
        # 환경변수 매핑 등록 — 실행 환경에서 URL을 주입할 수 있게
        register_env_mapping("config", "XGEN_CORE_URL")
        register_env_mapping("documents", "XGEN_DOCUMENTS_URL", "DOCUMENTS_SERVICE_BASE_URL")
        register_env_mapping("mcp", "MCP_STATION_URL", "MCP_STATION_RAW_URL")

        # Docker Compose 기본값 등록 (환경변수가 없을 때 폴백)
        import os
        if not os.environ.get("XGEN_CORE_URL"):
            register_service("config", "http://xgen-core:8000")
        if not os.environ.get("XGEN_DOCUMENTS_URL") and not os.environ.get("DOCUMENTS_SERVICE_BASE_URL"):
            register_service("documents", "http://xgen-documents:8000")
        if not os.environ.get("MCP_STATION_URL") and not os.environ.get("MCP_STATION_RAW_URL"):
            register_service("mcp", "http://xgen-mcp-station:8000")

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
        # top-level stage_params가 있으면 hc에 병합 (DB 저장 구조 호환)
        top_sp = workflow_data.get("stage_params")
        if top_sp and isinstance(top_sp, dict):
            existing_sp = hc.get("stage_params", {})
            existing_sp.update(top_sp)
            hc["stage_params"] = existing_sp
        if runtime_harness_config:
            hc.update(runtime_harness_config)

        agent_config = self._extract_agent_config(workflow_data)

        provider = hc.get("provider") or (agent_config or {}).get("provider", "anthropic")
        model = hc.get("model") or (agent_config or {}).get("model", "claude-sonnet-4-20250514")
        temperature = hc.get("temperature") if hc.get("temperature") is not None else 0.7
        system_prompt = hc.get("system_prompt") or (agent_config or {}).get("system_prompt", "")

        # ━━━━ 3. API 키 해석 — ExecutionContext → ServiceProvider → 환경변수 ━━━━
        api_key = ctx_get_api_key() or os.environ.get(get_api_key_env(provider), "")
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
        config_kwargs: Dict[str, Any] = {
            "provider": provider,
            "model": model,
            "temperature": float(temperature),
            "system_prompt": system_prompt,
        }
        # stage_params, disabled_stages, active_strategies 등 전달
        if hc.get("stage_params"):
            config_kwargs["stage_params"] = hc["stage_params"]
        if hc.get("disabled_stages"):
            config_kwargs["disabled_stages"] = hc["disabled_stages"]
        if hc.get("preset"):
            config_kwargs["preset"] = hc["preset"]
        if hc.get("max_iterations"):
            config_kwargs["max_iterations"] = hc["max_iterations"]

        config = HarnessConfig(**config_kwargs)

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

        # ━━━━ 7.5. xgen LLM Factory — xgen 프로바이더 직접 사용 ━━━━
        if self._llm_factory:
            try:
                xgen_llm = self._llm_factory(
                    provider=provider, model=model, api_key=api_key,
                    temperature=float(temperature), max_tokens=config.max_tokens,
                )
                from ..providers import wrap_langchain
                state.provider = wrap_langchain(xgen_llm, provider)
                logger.info("[Adapter] xgen LLM factory 사용: %s/%s", provider, model)
            except Exception as e:
                logger.warning("[Adapter] xgen LLM factory 실패, 내장 프로바이더 폴백: %s", e)

        # ━━━━ 8. API 키를 실행 컨텍스트에 설정 (contextvars — 동시 실행 격리) ━━━━
        set_execution_context(api_key=api_key, provider=provider, model=model)

        try:
            # ━━━━ 9. ResourceRegistry — xgen 자산 통합 로드 ━━━━
            registry = ResourceRegistry(self._services)
            await registry.load_all(workflow_data, hc)

            # 도구를 state에 바인딩
            state.tool_definitions = registry.get_tool_definitions()
            state.metadata["tool_registry"] = registry.get_tool_executors()
            state.metadata["resource_registry"] = registry

            # ━━━━ 10. 파이프라인 실행 ━━━━
            logger.info(
                "[Adapter] 실행: provider=%s, model=%s, tools=%d, rag=%d",
                provider, model, len(state.tool_definitions), len(registry.get_rag_collections()),
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
            pass  # contextvars는 자동 격리 — 복원 불필요

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

    # MCP 디스커버리는 ResourceRegistry.load_all()이 담당
