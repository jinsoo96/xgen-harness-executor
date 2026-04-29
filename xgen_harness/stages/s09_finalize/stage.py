"""
S09 Finalize — 최종 출력 포맷팅 + 메트릭스 + 저장 (v1.0)

v1.0 통합:
  - 구 s11_finalize → s09_finalize (번호 −2 시프트)
  - 구 s10_save stage 삭제 → 'persist' strategy 로 격하 흡수
  - 출력 포맷터는 외부 등록 가능 (박제 0): register_output_formatter()

Strategy:
  default  — 메트릭스 수집 + state.final_output 확정 (저장 X)
  persist  — default + DB 저장 (구 s10_save)
  noop     — 메트릭스만 발행, 출력 변형 X (디버깅)
"""

from __future__ import annotations

import logging
from typing import Callable

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState
from ...events.types import MetricsEvent

logger = logging.getLogger("harness.stage.finalize")


# ─── 출력 포맷터 레지스트리 (박제 풀기) ───────────────────────────────
# 키 = 포맷 이름, 값 = (state) -> str.
# stage_params.output_format 또는 active_strategies.s09_finalize 가
# 이 dict 의 키와 매칭되면 해당 포맷터 사용.

OutputFormatter = Callable[[PipelineState], str]


def _format_text(state: PipelineState) -> str:
    return state.final_output or state.last_assistant_text or ""


def _format_json(state: PipelineState) -> str:
    if not state.final_output:
        return ""
    import json as _json
    return _json.dumps(
        {
            "content": state.final_output,
            "model": state.provider.model_name if state.provider else "",
            "tokens": state.token_usage.total,
        },
        ensure_ascii=False,
        indent=2,
    )


def _format_markdown(state: PipelineState) -> str:
    if not state.final_output:
        return ""
    model = state.provider.model_name if state.provider else "unknown"
    return (
        f"## Response\n\n{state.final_output}\n\n---\n"
        f"*Model: {model} | Tokens: {state.token_usage.total}*"
    )


OUTPUT_FORMATTERS: dict[str, OutputFormatter] = {
    "text": _format_text,
    "json": _format_json,
    "markdown": _format_markdown,
}


def register_output_formatter(name: str, formatter: OutputFormatter) -> None:
    """출력 포맷터 등록. 외부 작업자가 자기 도메인 포맷 추가 가능.

    예) register_output_formatter("xml", lambda s: f"<r>{s.final_output}</r>")
        → stage_params.output_format = "xml" 로 선택.
    """
    OUTPUT_FORMATTERS[name] = formatter


_OUTPUT_FORMATTERS_DISCOVERED = False


def _discover_output_formatters_from_entry_points() -> None:
    """entry_points 그룹 ``xgen_harness.output_formatters`` 자동 발견. idempotent."""
    global _OUTPUT_FORMATTERS_DISCOVERED
    if _OUTPUT_FORMATTERS_DISCOVERED:
        return
    _OUTPUT_FORMATTERS_DISCOVERED = True
    try:
        from importlib.metadata import entry_points
    except Exception:
        return
    try:
        eps = entry_points()
        group = "xgen_harness.output_formatters"
        items = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])  # type: ignore[arg-type]
        for ep in items:
            try:
                fn = ep.load()
                if callable(fn):
                    register_output_formatter(ep.name, fn)
            except Exception as e:
                logger.warning("[output_formatters] entry_point %s 로드 실패: %s", ep.name, e)
    except Exception as e:
        logger.debug("[output_formatters] entry_points discovery 실패: %s", e)


_discover_output_formatters_from_entry_points()


class FinalizeStage(Stage):
    """최종 출력 포맷팅 + 메트릭스 + 선택적 DB 저장 (v1.0)."""

    @property
    def stage_id(self) -> str:
        return "s09_finalize"

    @property
    def order(self) -> int:
        return 9

    async def execute(self, state: PipelineState) -> dict:
        # 1. 최종 출력 확정 — 포맷터 결정
        # 우선순위: stage_params.output_format > active_strategies(이름이 포맷이면)
        strategy_name = (self.get_param("strategy", state, None) or "").strip().lower()
        fmt_name = self.get_param("output_format", state, "text")
        if not isinstance(fmt_name, str) or not fmt_name:
            fmt_name = "text"
        formatter = OUTPUT_FORMATTERS.get(fmt_name, OUTPUT_FORMATTERS["text"])

        if strategy_name == "noop":
            # 변형 없이 last_assistant_text 그대로 (디버깅 모드)
            state.final_output = state.last_assistant_text or ""
        else:
            state.final_output = formatter(state)

        # 2. 메트릭스 이벤트
        metrics = self._build_metrics(state)
        if state.event_emitter:
            await state.event_emitter.emit(MetricsEvent(**metrics))
        logger.info(
            "[Finalize] %dms, %d tokens, $%.4f, %d LLM calls, %d tools, %d iterations",
            metrics["duration_ms"],
            metrics["total_tokens"],
            metrics["cost_usd"],
            metrics["llm_calls"],
            metrics["tools_executed"],
            metrics["iterations"],
        )

        result: dict = {
            "output_length": len(state.final_output),
            "format": fmt_name,
            "usage": {
                "input_tokens": state.token_usage.input_tokens,
                "output_tokens": state.token_usage.output_tokens,
            },
            **metrics,
        }

        # 3. persist strategy — DB 저장 (구 s10_save 격하 흡수)
        if strategy_name == "persist" or self.get_param("save_enabled", state, False):
            from .strategies.persist import persist_execution_record
            persist_result = await persist_execution_record(state, self.get_param)
            result["persisted"] = persist_result

        return result

    def _build_metrics(self, state: PipelineState) -> dict:
        return {
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

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("default", "메트릭스 수집 + 출력 포맷팅", is_default=True),
            StrategyInfo("persist", "default + DB 저장 (구 s10_save 흡수)"),
            StrategyInfo("noop", "메트릭스만, 출력 변형 X (디버깅)"),
        ]


# 하위 호환 — 외부 import 보호
CompleteStage = FinalizeStage
