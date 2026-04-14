"""
S01 Input — 입력 검증 및 정규화

- 사용자 입력 텍스트 검증
- 첨부 파일 처리
- LLM 프로바이더 초기화 (API 키 확인)
- MCP 세션 ID 수집
"""

import logging
import os
from typing import Any, Optional

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..errors import ConfigError, PipelineAbortError
from ..providers.anthropic import AnthropicProvider
from ..providers.openai import OpenAIProvider

logger = logging.getLogger("harness.stage.input")

# 환경변수에서 API 키 읽기 순서: 환경변수 → xgen-core config API (향후)
_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
}


class InputStage(Stage):
    """입력 검증 + 프로바이더 초기화"""

    @property
    def stage_id(self) -> str:
        return "s01_input"

    @property
    def order(self) -> int:
        return 1

    async def execute(self, state: PipelineState) -> dict:
        config = state.config
        if not config:
            raise PipelineAbortError("Config not set", self.stage_id)

        # 1. 입력 검증
        if not state.user_input and not state.attached_files:
            raise ConfigError("입력이 비어있습니다", self.stage_id)

        # 2. stage_params에서 LLM 설정 오버라이드 (3-level fallback)
        provider_name: str = self.get_param("provider", state, config.provider)
        model_name: str = self.get_param("model", state, config.model)
        temperature: float = float(self.get_param("temperature", state, config.temperature))

        # config에도 반영 (다른 스테이지에서 config.provider 등 참조하는 경우 대비)
        config.provider = provider_name
        config.model = model_name
        config.temperature = temperature

        # 3. API 키 확인 + 프로바이더 초기화
        api_key = self._resolve_api_key(provider_name)
        if not api_key:
            raise ConfigError(
                f"{provider_name} API 키가 설정되지 않았습니다. "
                f"환경변수 {_API_KEY_ENV.get(provider_name, '?')}를 확인하세요.",
                self.stage_id,
            )

        state.provider = self._create_provider(provider_name, api_key, model_name)

        # 4. 사용자 메시지 추가
        user_content = self._build_user_content(state)
        state.add_message("user", user_content)

        # 5. MCP 세션 수집 + 도구 자동 디스커버리
        #    워크플로우 노드에서 기본 MCP 세션을 수집 (하위 호환)
        #    s04_tool_index에서 stage_params의 mcp_sessions로 추가 세션 디스커버리 가능
        mcp_sessions = self._collect_mcp_sessions(state.workflow_data)
        if mcp_sessions:
            await self._discover_mcp_tools(mcp_sessions, state)

        # 6. 도구 정의 수집 결과
        tools_count = len(state.tool_definitions)

        result = {
            "provider": provider_name,
            "model": model_name,
            "temperature": temperature,
            "input_length": len(state.user_input),
            "files_count": len(state.attached_files),
            "mcp_sessions": len(mcp_sessions),
            "tools_count": tools_count,
        }

        logger.info(
            "[Input] provider=%s, model=%s, temp=%.1f, input=%d chars, files=%d, tools=%d",
            provider_name, model_name, temperature,
            len(state.user_input), len(state.attached_files), tools_count,
        )
        return result

    async def _discover_mcp_tools(self, session_ids: list[str], state: PipelineState) -> None:
        """MCP 세션에서 도구를 자동 수집하여 state에 등록"""
        try:
            from ..tools.mcp_client import discover_mcp_tools
            mcp_tools = await discover_mcp_tools(session_ids)

            tool_mapping = {}
            for tool in mcp_tools:
                # Anthropic API 포맷으로 tool_definitions에 추가
                state.tool_definitions.append(tool.to_api_format())
                # MCP 도구 매핑 (실행 시 세션으로 라우팅)
                tool_mapping[tool.name] = tool._session_id

                # Tool 인스턴스도 레지스트리에 등록
                if "tool_registry" not in state.metadata:
                    state.metadata["tool_registry"] = {}
                state.metadata["tool_registry"][tool.name] = tool

            state.metadata["mcp_tool_mapping"] = tool_mapping
            logger.info("[Input] MCP tools: %d from %d sessions", len(mcp_tools), len(session_ids))
        except Exception as e:
            logger.warning("[Input] MCP tool discovery failed: %s", e)

    def _resolve_api_key(self, provider: str) -> Optional[str]:
        """API 키 해석: 환경변수 우선"""
        env_var = _API_KEY_ENV.get(provider)
        if env_var:
            key = os.environ.get(env_var, "")
            if key:
                return key

        # 폴백: 파일 기반 (xgen-core 패턴)
        key_files = {
            "anthropic": "/app/config/anthropic_api_key.txt",
            "openai": "/app/config/openai_api_key.txt",
            "google": "/app/config/gemini_api_key.txt",
        }
        filepath = key_files.get(provider, "")
        if filepath and os.path.exists(filepath):
            with open(filepath) as f:
                return f.read().strip()

        return None

    def _create_provider(self, provider_name: str, api_key: str, model: str):
        """프로바이더 인스턴스 생성"""
        if provider_name == "anthropic":
            base_url = os.environ.get("ANTHROPIC_API_BASE_URL")
            return AnthropicProvider(api_key, model, base_url)
        elif provider_name == "openai":
            base_url = os.environ.get("OPENAI_API_BASE_URL")
            return OpenAIProvider(api_key, model, base_url)
        else:
            # 미지원 프로바이더는 일단 OpenAI 호환으로 시도
            logger.warning("Unknown provider %s, trying OpenAI-compatible", provider_name)
            return OpenAIProvider(api_key, model)

    def _build_user_content(self, state: PipelineState) -> Any:
        """사용자 입력을 Anthropic content 포맷으로 변환"""
        if not state.attached_files:
            return state.user_input

        # 멀티모달: 텍스트 + 파일
        content_blocks = []
        for f in state.attached_files:
            if f.get("is_image"):
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": f.get("content_type", "image/png"),
                        "data": f.get("content", ""),
                    },
                })
            else:
                # 텍스트 파일은 프롬프트에 인라인
                file_text = f.get("text_content", f.get("content", ""))
                if file_text:
                    content_blocks.append({
                        "type": "text",
                        "text": f"[파일: {f.get('name', 'unknown')}]\n{file_text}",
                    })

        content_blocks.append({"type": "text", "text": state.user_input})
        return content_blocks

    def _collect_mcp_sessions(self, workflow_data: dict) -> list[str]:
        """워크플로우에서 MCP 세션 ID 수집"""
        sessions = []
        for node in workflow_data.get("nodes", []):
            data = node.get("data", {})
            if not data.get("id", "").startswith("mcp/"):
                continue
            params = data.get("parameters", [])
            for p in params:
                if p.get("id") == "session_id":
                    sid = p.get("value")
                    if sid and sid != "Select Session" and sid not in sessions:
                        sessions.append(sid)
        return sessions

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "기본 검증 + 프로바이더 초기화", is_default=True),
            StrategyInfo("with_classification", "입력 복잡도 자동 분류 포함"),
        ]
