"""
xgen-workflow SSE 포맷 변환

HarnessEvent → harness_router.py가 이해하는 dict 포맷으로 변환.
기존 _convert_harness_event()와 동일한 출력 생성.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from ..events.types import (
    HarnessEvent,
    StageEnterEvent,
    StageExitEvent,
    MessageEvent,
    ToolCallEvent,
    ToolResultEvent,
    EvaluationEvent,
    MetricsEvent,
    ErrorEvent,
    DoneEvent,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def convert_to_xgen_event(event: HarnessEvent) -> Optional[dict[str, Any]]:
    """HarnessEvent를 xgen-workflow SSE 이벤트 dict로 변환.

    Returns:
        {"type": "log"|"data"|"tool"|"error"|"end", "data": {...}}
        또는 None (무시할 이벤트)
    """

    if isinstance(event, StageEnterEvent):
        return {
            "type": "log",
            "data": {
                "level": "INFO",
                "message": f"[HARNESS] {event.stage_name} 시작",
                "node_name": "Harness",
                "timestamp": event.timestamp,
                "event_kind": "stage_enter",
                "stage_id": event.stage_id,
                "stage_name": event.stage_name,
                "phase": event.phase,
                "step": event.step,
                "total": event.total,
            },
        }

    elif isinstance(event, StageExitEvent):
        return {
            "type": "log",
            "data": {
                "level": "INFO",
                "message": f"[HARNESS] {event.stage_name} 완료",
                "node_name": "Harness",
                "timestamp": event.timestamp,
                "event_kind": "stage_exit",
                "stage_id": event.stage_id,
                "stage_name": event.stage_name,
                "output": event.output,
                "score": event.score,
                "step": event.step,
                "total": event.total,
            },
        }

    elif isinstance(event, MessageEvent):
        return {
            "type": "data",
            "data": {
                "type": "stream",
                "content": event.text,
                "role": event.role,
                "timestamp": event.timestamp,
            },
        }

    elif isinstance(event, ToolCallEvent):
        return {
            "type": "tool",
            "data": {
                "event": "call",
                "tool_use_id": event.tool_use_id,
                "tool_name": event.tool_name,
                "tool_input": event.tool_input,
                "timestamp": event.timestamp,
            },
        }

    elif isinstance(event, ToolResultEvent):
        return {
            "type": "tool",
            "data": {
                "event": "result",
                "tool_use_id": event.tool_use_id,
                "tool_name": event.tool_name,
                "result": event.result,
                "is_error": event.is_error,
                "timestamp": event.timestamp,
            },
        }

    elif isinstance(event, EvaluationEvent):
        return {
            "type": "log",
            "data": {
                "level": "INFO",
                "message": f"[HARNESS] 검증 점수 {event.score:.2f} → {event.verdict}",
                "node_name": "Harness",
                "timestamp": event.timestamp,
                "event_kind": "evaluation",
                "score": event.score,
                "feedback": event.feedback,
                "verdict": event.verdict,
            },
        }

    elif isinstance(event, MetricsEvent):
        return {
            "type": "log",
            "data": {
                "level": "INFO",
                "message": (
                    f"[HARNESS] 완료: {event.duration_ms}ms, "
                    f"{event.total_tokens} tokens, ${event.cost_usd:.4f}"
                ),
                "node_name": "Harness",
                "timestamp": event.timestamp,
                "event_kind": "metrics",
                "duration_ms": event.duration_ms,
                "total_tokens": event.total_tokens,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cost_usd": event.cost_usd,
                "llm_calls": event.llm_calls,
                "tools_executed": event.tools_executed,
                "iterations": event.iterations,
                "model": event.model,
            },
        }

    elif isinstance(event, ErrorEvent):
        return {
            "type": "error",
            "data": {
                "message": event.message,
                "stage_id": event.stage_id,
                "recoverable": event.recoverable,
                "timestamp": event.timestamp,
            },
        }

    elif isinstance(event, DoneEvent):
        return {
            "type": "end",
            "data": {
                "final_output": event.final_output,
                "success": event.success,
                "timestamp": event.timestamp,
            },
        }

    return None
