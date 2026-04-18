"""
하네스 이벤트 타입 정의

xgen-workflow의 SSE 포맷과 호환되는 이벤트 구조.
harness_router.py의 _convert_harness_event()가 이 이벤트를 받아 변환.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HarnessEvent:
    """모든 하네스 이벤트의 기반"""
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class StageEnterEvent(HarnessEvent):
    """스테이지 시작"""
    stage_id: str = ""
    stage_name: str = ""        # display name (한국어 or 영어)
    phase: str = ""             # "ingress" | "loop" | "egress"
    step: int = 0               # 현재 스텝 (1-indexed)
    total: int = 0              # 전체 스테이지 수
    description: str = ""


@dataclass
class StageExitEvent(HarnessEvent):
    """스테이지 완료"""
    stage_id: str = ""
    stage_name: str = ""
    output: dict = field(default_factory=dict)
    score: Optional[float] = None
    step: int = 0
    total: int = 0


@dataclass
class MessageEvent(HarnessEvent):
    """LLM 스트리밍 텍스트 청크"""
    text: str = ""
    role: str = "assistant"
    is_final: bool = False


@dataclass
class ThinkingEvent(HarnessEvent):
    """Extended thinking 블록"""
    text: str = ""


@dataclass
class ToolCallEvent(HarnessEvent):
    """LLM이 도구 호출 요청"""
    tool_use_id: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)


@dataclass
class ToolResultEvent(HarnessEvent):
    """도구 실행 결과"""
    tool_use_id: str = ""
    tool_name: str = ""
    result: str = ""
    is_error: bool = False


@dataclass
class EvaluationEvent(HarnessEvent):
    """검증 스테이지 평가 결과"""
    score: float = 0.0
    feedback: str = ""
    verdict: str = ""           # "pass" | "retry" | "fail"


@dataclass
class MetricsEvent(HarnessEvent):
    """최종 메트릭스"""
    duration_ms: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    tools_executed: int = 0
    iterations: int = 0
    model: str = ""


@dataclass
class ErrorEvent(HarnessEvent):
    """에러 발생"""
    message: str = ""
    stage_id: str = ""
    recoverable: bool = False


@dataclass
class DoneEvent(HarnessEvent):
    """파이프라인 완료"""
    final_output: str = ""
    success: bool = True


@dataclass
class MissingParamEvent(HarnessEvent):
    """필수 파라미터 누락 — 사용자/상위 시스템에 되물음 신호"""
    capability: str = ""         # capability name
    tool_name: str = ""
    param_name: str = ""
    param_type: str = ""
    description: str = ""
    source_hint: str = ""        # 어디서 찾으려 했는지 ("user_input" 등)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Verbose 이벤트 — HarnessConfig.verbose_events=True 시 발행
#  운영/디버깅 시 Redis vs env, capability 바인딩 경로, 스테이지 substep 가시화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class ServiceLookupEvent(HarnessEvent):
    """설정/키 조회 경로 추적 — Redis 우선 정책이 실제 작동하는지 가시화"""
    key: str = ""                          # "ANTHROPIC_API_KEY" 등
    source: str = ""                       # "context" | "redis" | "env" | "fallback" | "missing"
    hit: bool = False
    provider: str = ""                     # 프로바이더 스코프 (있을 때)


@dataclass
class CapabilityBindEvent(HarnessEvent):
    """capability 바인딩 — 선언/발견/자동발행 3경로 중 어느 것"""
    name: str = ""
    source: str = ""                       # "declaration" | "discovery" | "auto_publish"
    score: Optional[float] = None
    stage_id: str = ""


@dataclass
class StageSubstepEvent(HarnessEvent):
    """스테이지 내부 단계 — 스테이지 블랙박스 해소"""
    stage_id: str = ""
    substep: str = ""                      # "rag_fetch_start" / "llm_request" / "tool_exec" 등
    duration_ms: Optional[int] = None
    meta: dict = field(default_factory=dict)


@dataclass
class RetryEvent(HarnessEvent):
    """재시도/폴백 — 어떤 스테이지/이유/몇 번째 시도"""
    stage_id: str = ""
    reason: str = ""
    attempt: int = 1
    max_attempts: int = 1


def event_to_dict(event: HarnessEvent) -> dict[str, Any]:
    """이벤트를 harness_router.py가 이해하는 (event_type, data) dict로 변환"""
    type_map = {
        StageEnterEvent: "stage_enter",
        StageExitEvent: "stage_exit",
        MessageEvent: "message",
        ThinkingEvent: "thinking",
        ToolCallEvent: "tool_call",
        ToolResultEvent: "tool_result",
        EvaluationEvent: "evaluation",
        MetricsEvent: "metrics",
        ErrorEvent: "error",
        DoneEvent: "done",
        MissingParamEvent: "missing_param",
        ServiceLookupEvent: "service_lookup",
        CapabilityBindEvent: "capability_bind",
        StageSubstepEvent: "stage_substep",
        RetryEvent: "retry",
    }
    event_type = type_map.get(type(event), "unknown")

    data = {}
    for k, v in event.__dict__.items():
        if k == "timestamp":
            continue
        if v is not None and v != "" and v != 0 and v != {} and v != 0.0:
            data[k] = v
        elif k in ("is_error", "is_final", "success", "recoverable"):
            data[k] = v

    data["timestamp"] = event.timestamp
    return {"event_type": event_type, "data": data}
