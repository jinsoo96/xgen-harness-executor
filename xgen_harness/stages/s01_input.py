"""
S01 Input — 사용자 입력 정규화

PHILOSOPHY §2 s01 "담당":
  - 텍스트/파일 입력 검증 (빈 입력 거부)
  - 첨부 파일 → content block 변환 (base64 이미지, 텍스트 첨부)
  - ``state.messages`` 에 첫 user 메시지 push
  - 선택적 입력 복잡도 분류 (``with_classification`` Strategy)

PHILOSOPHY §2 s01 "비담당" (v0.9.0+):
  - LLM provider 생성 / API key / base_url 해석 → **s07_llm** 으로 이관
  - MCP 도구 디스커버리 → **s04_tool_index** 로 이관

provider / model / temperature 는 config 에만 기록 — 해석 시점은 s07.
"""

import logging
from typing import Any

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..errors import ConfigError, PipelineAbortError

logger = logging.getLogger("harness.stage.input")


class InputStage(Stage):
    """입력 정규화 전용 Stage."""

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

        if not state.user_input and not state.attached_files:
            raise ConfigError("입력이 비어있습니다", self.stage_id)

        # stage_params → config 반영만. provider 생성은 s07 이 담당.
        config.provider = self.get_param("provider", state, config.provider)
        config.model = self.get_param("model", state, config.model)
        config.temperature = float(self.get_param("temperature", state, config.temperature))

        # 사용자 메시지 push
        state.add_message("user", self._build_user_content(state))

        # 입력 복잡도 분류 (Strategy)
        strategy_name = self.get_param("strategy", state, "default")
        input_complexity = None
        if strategy_name == "with_classification":
            input_complexity = self._classify_input(state.user_input)
            state.metadata["input_complexity"] = input_complexity
            logger.info("[Input] complexity=%s", input_complexity)

        result: dict[str, Any] = {
            "provider": config.provider,
            "model": config.model,
            "temperature": config.temperature,
            "input_length": len(state.user_input),
            "files_count": len(state.attached_files),
        }
        if input_complexity:
            result["input_complexity"] = input_complexity

        logger.info(
            "[Input] provider=%s, model=%s, temp=%.1f, input=%d chars, files=%d",
            config.provider, config.model, config.temperature,
            len(state.user_input), len(state.attached_files),
        )
        return result

    def _classify_input(self, text: str) -> str:
        """입력 복잡도 분류: simple / moderate / complex (휴리스틱, LLM 호출 없음)."""
        if not text:
            return "simple"

        text_lower = text.lower()
        length = len(text)
        sentences = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
        sentence_count = len(sentences)

        score = 0
        if length > 500:
            score += 2
        elif length > 150:
            score += 1
        if sentence_count > 5:
            score += 2
        elif sentence_count > 2:
            score += 1

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

        conditional_markers = [
            "if ", "unless", "when ", "otherwise", "depending",
            "만약", "경우", "아니면", "조건",
        ]
        if any(m in text_lower for m in conditional_markers):
            score += 1

        question_count = text.count("?")
        if question_count > 2:
            score += 2
        elif question_count > 0:
            score += 1

        if score >= 5:
            return "complex"
        elif score >= 2:
            return "moderate"
        return "simple"

    def _build_user_content(self, state: PipelineState) -> Any:
        """사용자 입력을 content 포맷으로 변환."""
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

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "기본 입력 정규화", is_default=True),
            StrategyInfo("with_classification", "입력 복잡도 자동 분류 포함"),
        ]
