"""
HarnessConfig — 파이프라인 설정

프리셋 없음. 12개 스테이지 전체 기본 활성, 개별 토글로 on/off.
workflow_data.harness_config에서 로드.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# 전체 12 스테이지 (기본 전부 활성)
ALL_STAGES = [
    "s01_input",
    "s02_memory",
    "s03_system_prompt",
    "s04_tool_index",
    "s05_plan",
    "s06_context",
    "s07_llm",
    "s08_execute",
    "s09_validate",
    "s10_decide",
    "s11_save",
    "s12_complete",
]

# 비활성화 불가 스테이지
REQUIRED_STAGES = {"s01_input", "s07_llm", "s10_decide", "s12_complete"}


@dataclass
class HarnessConfig:
    """하네스 파이프라인 설정 — 프리셋 없음, 스테이지 개별 토글"""

    # --- LLM ---
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.7
    max_tokens: int = 8192

    # --- 폴백 모델 ---
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-sonnet-4-20250514"

    # --- 루프 제어 ---
    max_iterations: int = 10
    max_tool_rounds: int = 20
    max_retries: int = 3
    validation_threshold: float = 0.7

    # --- 시스템 프롬프트 ---
    system_prompt: str = ""

    # --- 스테이지 토글 (False = 비활성) ---
    disabled_stages: set = field(default_factory=set)

    # --- 스테이지별 아티팩트 선택 ---
    artifacts: dict = field(default_factory=dict)  # stage_id → artifact_name

    # --- 스테이지별 파라미터 (UI에서 설정, 런타임에 반영) ---
    stage_params: dict = field(default_factory=dict)  # stage_id → {field_id: value}

    # --- Strategy 선택 (UI에서 클릭, stage_id → strategy impl_name) ---
    active_strategies: dict = field(default_factory=dict)  # stage_id → impl_name

    # --- 기타 ---
    cost_budget_usd: float = 10.0
    context_window: int = 200_000
    thinking_enabled: bool = False
    thinking_budget_tokens: int = 10000

    # 레거시 호환
    preset: str = ""

    def get_active_stage_ids(self) -> list[str]:
        """활성 스테이지 ID 목록"""
        return [s for s in ALL_STAGES if s not in self.disabled_stages]

    def is_stage_active(self, stage_id: str) -> bool:
        return stage_id not in self.disabled_stages

    def toggle_stage(self, stage_id: str, active: bool) -> None:
        """스테이지 활성/비활성 토글. 필수 스테이지는 비활성화 불가."""
        if stage_id in REQUIRED_STAGES and not active:
            return
        if active:
            self.disabled_stages.discard(stage_id)
        else:
            self.disabled_stages.add(stage_id)

    def get_artifact_for_stage(self, stage_id: str) -> str:
        return self.artifacts.get(stage_id, "default")

    @classmethod
    def from_workflow(cls, harness_config: dict[str, Any], workflow_data: dict[str, Any]) -> "HarnessConfig":
        """workflow_data에서 설정 생성"""
        agent_config = _extract_agent_config_from_nodes(workflow_data)

        # 비활성 스테이지
        disabled = set()
        disabled_list = harness_config.get("disabled_stages", [])
        if isinstance(disabled_list, list):
            disabled = set(disabled_list) - REQUIRED_STAGES

        # 레거시 preset 호환
        preset = harness_config.get("preset", "")
        if preset and not disabled_list:
            # 프리셋이 있으면 무시 (전부 활성)
            pass

        return cls(
            provider=harness_config.get("provider") or agent_config.get("provider", "anthropic"),
            model=harness_config.get("model") or agent_config.get("model", "claude-sonnet-4-20250514"),
            temperature=float(harness_config.get("temperature", agent_config.get("temperature", 0.7))),
            max_tokens=int(harness_config.get("max_tokens", 8192)),
            openai_model=harness_config.get("openai_model") or agent_config.get("openai_model", "gpt-4o-mini"),
            anthropic_model=harness_config.get("anthropic_model") or agent_config.get("anthropic_model", "claude-sonnet-4-20250514"),
            system_prompt=harness_config.get("system_prompt") or agent_config.get("system_prompt", ""),
            max_iterations=int(harness_config.get("max_iterations", 10)),
            max_retries=int(harness_config.get("max_retries", 3)),
            validation_threshold=float(harness_config.get("validation_threshold", 0.7)),
            disabled_stages=disabled,
            artifacts=harness_config.get("artifacts", {}),
            stage_params=harness_config.get("stage_params", {}),
            active_strategies=harness_config.get("active_strategies", {}),
            thinking_enabled=bool(harness_config.get("thinking_enabled", False)),
            thinking_budget_tokens=int(harness_config.get("thinking_budget_tokens", 10000)),
            preset=preset,
        )


def _extract_agent_config_from_nodes(workflow_data: dict) -> dict[str, Any]:
    """에이전트 노드에서 provider/model/system_prompt 추출"""
    for node in workflow_data.get("nodes", []):
        data = node.get("data", {})
        if not data.get("id", "").startswith("agents/"):
            continue
        parameters = data.get("parameters", [])
        if not isinstance(parameters, list):
            continue

        def _get(pid: str) -> str:
            for p in parameters:
                if p.get("id") == pid:
                    v = p.get("value")
                    return str(v) if v is not None else ""
            return ""

        provider = _get("provider")
        model = _get("model")
        if not model:
            if provider == "openai":
                model = _get("openai_model") or "gpt-4o-mini"
            elif provider == "anthropic":
                model = _get("anthropic_model") or "claude-sonnet-4-20250514"

        return {
            "provider": provider,
            "model": model,
            "openai_model": _get("openai_model") or "gpt-4o-mini",
            "anthropic_model": _get("anthropic_model") or "claude-sonnet-4-20250514",
            "system_prompt": _get("system_prompt"),
            "temperature": _get("temperature") or "0.7",
        }
    return {}
