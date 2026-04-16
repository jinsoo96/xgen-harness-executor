"""
S12 Complete — 최종 출력 포맷팅

- 최종 텍스트 출력 확정
- 메트릭스 수집 및 이벤트 발행
- 실행 결과 정리
"""

import logging

from ..core.stage import Stage, StrategyInfo
from ..core.state import PipelineState
from ..events.types import MetricsEvent

logger = logging.getLogger("harness.stage.complete")


class CompleteStage(Stage):
    """최종 출력 포맷팅 + 메트릭스"""

    @property
    def stage_id(self) -> str:
        return "s12_complete"

    @property
    def order(self) -> int:
        return 12

    async def execute(self, state: PipelineState) -> dict:
        # 최종 출력 확정
        state.final_output = state.last_assistant_text or ""

        # output_format 적용
        fmt = self.get_param("output_format", state, "text")
        if fmt == "json" and state.final_output:
            import json as _json
            state.final_output = _json.dumps({
                "content": state.final_output,
                "model": state.provider.model_name if state.provider else "",
                "tokens": state.token_usage.total,
            }, ensure_ascii=False, indent=2)
        elif fmt == "markdown" and state.final_output:
            state.final_output = f"## Response\n\n{state.final_output}\n\n---\n*Model: {state.provider.model_name if state.provider else 'unknown'} | Tokens: {state.token_usage.total}*"

        # 메트릭스 이벤트 발행
        metrics = {
            "duration_ms": state.elapsed_ms,
            "total_tokens": state.token_usage.total,
            "input_tokens": state.token_usage.input_tokens,
            "output_tokens": state.token_usage.output_tokens,
            "cost_usd": round(state.cost_usd, 6),
            "llm_calls": state.llm_call_count,
            "tools_executed": state.tools_executed_count,
            "iterations": state.loop_iteration,
            "model": state.provider.model_name if state.provider else "",
        }

        if state.event_emitter:
            await state.event_emitter.emit(MetricsEvent(**metrics))

        logger.info(
            "[Complete] %dms, %d tokens, $%.4f, %d LLM calls, %d tools, %d iterations",
            metrics["duration_ms"],
            metrics["total_tokens"],
            metrics["cost_usd"],
            metrics["llm_calls"],
            metrics["tools_executed"],
            metrics["iterations"],
        )

        return {
            "output_length": len(state.final_output),
            "usage": {
                "input_tokens": state.token_usage.input_tokens,
                "output_tokens": state.token_usage.output_tokens,
            },
            **metrics,
        }

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "메트릭스 수집 + 최종 출력", is_default=True),
            StrategyInfo("format_json", "JSON 구조화 출력"),
        ]
