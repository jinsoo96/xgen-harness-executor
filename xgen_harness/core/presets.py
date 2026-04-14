"""
Preset 시스템 — 스테이지 활성/비활성 + Strategy 일괄 변경

geny-harness 패턴: minimal, chat, agent, evaluator, vtuber
각 Preset은 12개 스테이지 중 어떤 것을 켜고 끌지, Strategy를 뭘로 할지 정의.

사용:
    config = HarnessConfig.from_preset("agent")
    # → s02_memory ON, s04_tool_index ON, s05_plan ON, s09_validate ON
    # → s10_decide: "threshold", s09: "llm_judge"
"""

from dataclasses import dataclass, field
from typing import Any

from .config import ALL_STAGES, REQUIRED_STAGES


@dataclass
class Preset:
    """프리셋 정의"""
    name: str
    description: str
    description_ko: str
    # 비활성화할 스테이지 (REQUIRED_STAGES는 비활성화 불가)
    disabled_stages: set[str] = field(default_factory=set)
    # Strategy 선택
    active_strategies: dict[str, str] = field(default_factory=dict)
    # 기본 stage_params
    default_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 기본 설정
    temperature: float = 0.7
    max_iterations: int = 10


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  내장 프리셋
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PRESETS: dict[str, Preset] = {
    "minimal": Preset(
        name="minimal",
        description="Minimal chat — no tools, no RAG, no validation",
        description_ko="최소 채팅 — 도구/RAG/검증 없이 바로 대화",
        disabled_stages={"s02_memory", "s04_tool_index", "s05_plan", "s06_context", "s08_execute", "s09_validate", "s11_save"},
        active_strategies={"s10_decide": "always_pass"},
        temperature=0.7,
        max_iterations=1,
    ),

    "chat": Preset(
        name="chat",
        description="Chat with memory — conversation history maintained",
        description_ko="대화형 — 이전 대화 이력 유지, 멀티턴",
        disabled_stages={"s04_tool_index", "s05_plan", "s06_context", "s08_execute", "s09_validate", "s11_save"},
        active_strategies={"s10_decide": "always_pass"},
        temperature=0.7,
        max_iterations=1,
    ),

    "agent": Preset(
        name="agent",
        description="Full agent — tools, RAG, planning, validation, loop",
        description_ko="에이전트 — 도구 사용, RAG, 계획, 검증, 루프",
        disabled_stages=set(),  # 전체 활성
        active_strategies={
            "s04_tool_index": "progressive_3level",
            "s09_validate": "rule_based",
            "s10_decide": "threshold",
        },
        temperature=0.3,
        max_iterations=10,
    ),

    "evaluator": Preset(
        name="evaluator",
        description="Evaluator agent — strict validation with LLM judge",
        description_ko="평가형 — LLM Judge로 엄격한 품질 검증",
        disabled_stages=set(),
        active_strategies={
            "s04_tool_index": "progressive_3level",
            "s09_validate": "llm_judge",
            "s10_decide": "threshold",
        },
        default_params={
            "s09_validate": {"threshold": 0.8},
        },
        temperature=0.2,
        max_iterations=15,
    ),

    "rag": Preset(
        name="rag",
        description="RAG-focused — document search, no tools",
        description_ko="RAG 전용 — 문서 검색 기반 답변, 도구 없음",
        disabled_stages={"s04_tool_index", "s05_plan", "s08_execute", "s09_validate"},
        active_strategies={"s10_decide": "always_pass"},
        temperature=0.3,
        max_iterations=1,
    ),
}


def get_preset(name: str) -> Preset | None:
    return PRESETS.get(name)


def list_presets() -> list[dict[str, Any]]:
    """프리셋 목록 반환 (API/UI용)"""
    return [
        {
            "name": p.name,
            "description": p.description,
            "description_ko": p.description_ko,
            "disabled_stages": list(p.disabled_stages),
            "active_strategies": p.active_strategies,
            "temperature": p.temperature,
            "max_iterations": p.max_iterations,
        }
        for p in PRESETS.values()
    ]


def apply_preset(config, preset_name: str) -> None:
    """HarnessConfig에 프리셋 적용"""
    preset = get_preset(preset_name)
    if not preset:
        return

    config.disabled_stages = preset.disabled_stages - REQUIRED_STAGES
    config.active_strategies = dict(preset.active_strategies)
    config.temperature = preset.temperature
    config.max_iterations = preset.max_iterations

    # stage_params 병합 (기존 설정 유지, 프리셋 기본값 추가)
    for stage_id, params in preset.default_params.items():
        existing = config.stage_params.get(stage_id, {})
        merged = {**params, **existing}  # 기존 설정이 우선
        config.stage_params[stage_id] = merged
