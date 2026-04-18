"""
S01 Input — 입력 검증 및 정규화

- 사용자 입력 텍스트 검증
- 첨부 파일 처리
- LLM 프로바이더 초기화 (레지스트리 기반)
- ServiceProvider를 통한 API 키 해석
- MCP 도구 자동 디스커버리
"""

import logging
import os
from typing import Any, Optional

from ..core.execution_context import get_api_key as ctx_get_api_key
from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..errors import ConfigError, PipelineAbortError
from ..providers import create_provider, get_api_key_env, PROVIDER_API_KEY_MAP

logger = logging.getLogger("harness.stage.input")


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
        # model 이 비어있으면 providers 레지스트리의 기본값 사용 — 하드코딩 대신 단일 진실 소스
        if not model_name:
            from ..providers import PROVIDER_DEFAULT_MODEL
            model_name = PROVIDER_DEFAULT_MODEL.get(provider_name.lower(), "")
        temperature: float = float(self.get_param("temperature", state, config.temperature))

        config.provider = provider_name
        config.model = model_name
        config.temperature = temperature

        # 3. API 키 해석 — ServiceProvider 우선, 환경변수 폴백
        api_key = await self._resolve_api_key(provider_name, state)
        if not api_key:
            env_var = get_api_key_env(provider_name)
            raise ConfigError(
                f"{provider_name} API 키가 설정되지 않았습니다. "
                f"환경변수 {env_var}를 확인하세요.",
                self.stage_id,
            )

        # 3.5. base_url 해석 — Redis(xgen-core) → env 순서. providers/__init__.py:70
        # 의 env only fallback 은 그대로 두되, 여기서 선제 주입해 Redis 우선 정책 준수.
        base_url = await self._resolve_base_url(provider_name, state)

        # 4. 프로바이더 생성 — 레지스트리 기반 (if/elif 없음)
        state.provider = create_provider(provider_name, api_key, model_name, base_url=base_url)

        # 5. 사용자 메시지 추가
        user_content = self._build_user_content(state)
        state.add_message("user", user_content)

        # 6. MCP 도구 디스커버리 — ServiceProvider 우선, 레거시 폴백
        services = state.metadata.get("services")
        mcp_sessions = self._collect_mcp_sessions(state.workflow_data)

        if mcp_sessions and services and services.mcp:
            await self._discover_mcp_tools_via_service(mcp_sessions, state, services.mcp)
        elif mcp_sessions:
            await self._discover_mcp_tools_legacy(mcp_sessions, state)

        # 7. 입력 복잡도 분류 (with_classification 전략 선택 시)
        strategy_name = self.get_param("strategy", state, "default")
        input_complexity = None
        if strategy_name == "with_classification":
            input_complexity = self._classify_input(state.user_input)
            state.metadata["input_complexity"] = input_complexity
            logger.info("[Input] complexity=%s", input_complexity)

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
        if input_complexity:
            result["input_complexity"] = input_complexity

        logger.info(
            "[Input] provider=%s, model=%s, temp=%.1f, input=%d chars, files=%d, tools=%d",
            provider_name, model_name, temperature,
            len(state.user_input), len(state.attached_files), tools_count,
        )
        return result

    def _classify_input(self, text: str) -> str:
        """입력 복잡도 분류: simple / moderate / complex.

        휴리스틱 기반 (LLM 호출 없이):
        - 문장 수, 토큰 수, 질문 구조, 접속사/조건 키워드 등으로 판별
        - s05_plan에서 이 결과를 참조해 planning depth를 결정할 수 있음
        """
        if not text:
            return "simple"

        text_lower = text.lower()
        length = len(text)
        sentences = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
        sentence_count = len(sentences)

        # 복잡도 점수 계산
        score = 0

        # 길이 기반
        if length > 500:
            score += 2
        elif length > 150:
            score += 1

        # 문장 수 기반
        if sentence_count > 5:
            score += 2
        elif sentence_count > 2:
            score += 1

        # 멀티스텝 키워드 (언어 비의존적)
        multi_step_markers = [
            "then", "after that", "next", "finally", "first", "second", "third",
            "step 1", "step 2", "1.", "2.", "3.",
            "and also", "in addition", "moreover", "furthermore",
            "그 다음", "먼저", "그리고", "또한", "마지막으로",
        ]
        marker_count = sum(1 for m in multi_step_markers if m in text_lower)
        if marker_count >= 3:
            score += 2
        elif marker_count >= 1:
            score += 1

        # 조건문 키워드
        conditional_markers = [
            "if ", "unless", "when ", "otherwise", "depending",
            "만약", "경우", "아니면", "조건",
        ]
        if any(m in text_lower for m in conditional_markers):
            score += 1

        # 질문 다수
        question_count = text.count("?")
        if question_count > 2:
            score += 2
        elif question_count > 0:
            score += 1

        # 점수 → 복잡도 매핑
        if score >= 5:
            return "complex"
        elif score >= 2:
            return "moderate"
        return "simple"

    async def _resolve_base_url(self, provider: str, state: PipelineState) -> Optional[str]:
        """Provider base_url 해석: ServiceProvider(Redis 우선) → 환경변수 → None.

        None 을 반환하면 providers/__init__.py 의 env fallback + provider 기본값이 적용.
        Redis 에 ``{PROVIDER}_API_BASE_URL`` 가 있으면 부팅 고정 .env 보다 우선.
        """
        env_var = f"{provider.upper()}_API_BASE_URL"

        # 1. ServiceProvider (Redis → xgen-core persistent_configs)
        services = state.metadata.get("services")
        if services and services.config:
            try:
                # v2 의 get_setting 우선. 구버전 구현체는 get_value 폴백.
                get_setting = getattr(services.config, "get_setting", None)
                if get_setting is not None:
                    value = await get_setting(env_var)
                else:
                    value = await services.config.get_value(env_var, "")
                if value:
                    return value
            except Exception as e:
                logger.debug("[Input] base_url Redis 조회 실패 (%s): %s", env_var, e)

        # 2. 환경변수
        value = os.environ.get(env_var, "")
        if value:
            return value

        # 3. None → providers 가 자체 기본값 사용
        return None

    async def _resolve_api_key(self, provider: str, state: PipelineState) -> Optional[str]:
        """API 키 해석: ExecutionContext → ServiceProvider → 환경변수 → 파일 폴백"""
        # 1. ExecutionContext (contextvars — 동시 실행 안전)
        key = ctx_get_api_key()
        if key:
            return key

        # 2. ServiceProvider (xgen 환경)
        services = state.metadata.get("services")
        if services and services.config:
            try:
                key = await services.config.get_api_key(provider)
                if key:
                    return key
            except Exception as e:
                logger.debug("[Input] ServiceProvider API key lookup failed: %s", e)

        # 3. 환경변수 (읽기 전용 — 기존 설정 호환)
        env_var = get_api_key_env(provider)
        key = os.environ.get(env_var, "")
        if key:
            return key

        # 4. 파일 기반 폴백 (Docker 환경)
        filepath = f"/app/config/{env_var.lower()}.txt"
        if os.path.exists(filepath):
            with open(filepath) as f:
                return f.read().strip()

        return None

    async def _discover_mcp_tools_via_service(self, session_ids: list[str], state: PipelineState, mcp_service) -> None:
        """ServiceProvider.mcp를 통한 도구 디스커버리"""
        tool_mapping = {}
        for sid in session_ids:
            try:
                tools = await mcp_service.list_tools(sid)
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
                    tool_mapping[name] = sid
                logger.info("[Input] MCP session %s: %d tools via ServiceProvider", sid, len(tools))
            except Exception as e:
                logger.warning("[Input] MCP session %s discovery failed: %s", sid, e)

        if tool_mapping:
            state.metadata["mcp_tool_sessions"] = tool_mapping
            if "tool_registry" not in state.metadata:
                state.metadata["tool_registry"] = {}

    async def _discover_mcp_tools_legacy(self, session_ids: list[str], state: PipelineState) -> None:
        """레거시: mcp_client 직접 호출 (ServiceProvider 없을 때)"""
        try:
            from ..tools.mcp_client import discover_mcp_tools
            mcp_tools = await discover_mcp_tools(session_ids)

            tool_mapping = {}
            for tool in mcp_tools:
                state.tool_definitions.append(tool.to_api_format())
                tool_mapping[tool.name] = tool._session_id
                if "tool_registry" not in state.metadata:
                    state.metadata["tool_registry"] = {}
                state.metadata["tool_registry"][tool.name] = tool

            state.metadata["mcp_tool_mapping"] = tool_mapping
            logger.info("[Input] MCP tools (legacy): %d from %d sessions", len(mcp_tools), len(session_ids))
        except Exception as e:
            logger.warning("[Input] MCP tool discovery failed: %s", e)

    def _build_user_content(self, state: PipelineState) -> Any:
        """사용자 입력을 content 포맷으로 변환"""
        if not state.attached_files:
            return state.user_input

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
