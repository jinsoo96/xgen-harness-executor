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

    # --- Capability 선언 (capability name 리스트, s04_tool_index가 자동 바인딩) ---
    capabilities: list = field(default_factory=list)       # ["retrieval.web_search", ...]
    capability_params: dict = field(default_factory=dict)  # capability_name → {param_id: value}

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

    # ───────────────────────────────────────────────
    # 직렬화 — Builder 산출물을 파일로 저장/로드
    # ───────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """HarnessConfig 를 JSON-직렬화 가능한 dict 로 변환.

        set 타입(disabled_stages)은 정렬된 list 로 변환.
        Optional 필드는 기본값이면 생략하지 않음 — roundtrip 일관성 보장.
        """
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": float(self.temperature),
            "max_tokens": int(self.max_tokens),
            "openai_model": self.openai_model,
            "anthropic_model": self.anthropic_model,
            "max_iterations": int(self.max_iterations),
            "max_tool_rounds": int(self.max_tool_rounds),
            "max_retries": int(self.max_retries),
            "validation_threshold": float(self.validation_threshold),
            "system_prompt": self.system_prompt,
            "disabled_stages": sorted(self.disabled_stages),
            "artifacts": dict(self.artifacts),
            "stage_params": dict(self.stage_params),
            "active_strategies": dict(self.active_strategies),
            "capabilities": list(self.capabilities),
            "capability_params": dict(self.capability_params),
            "cost_budget_usd": float(self.cost_budget_usd),
            "context_window": int(self.context_window),
            "thinking_enabled": bool(self.thinking_enabled),
            "thinking_budget_tokens": int(self.thinking_budget_tokens),
            "preset": self.preset,
            "_schema_version": 1,
        }

    def to_json(self, indent: int = 2) -> str:
        """JSON 문자열로 직렬화."""
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, path: str) -> None:
        """JSON 파일로 저장."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HarnessConfig":
        """dict → HarnessConfig 역직렬화."""
        disabled = data.get("disabled_stages", [])
        if isinstance(disabled, list):
            disabled = set(disabled) - REQUIRED_STAGES
        elif isinstance(disabled, set):
            disabled = disabled - REQUIRED_STAGES
        else:
            disabled = set()

        return cls(
            provider=data.get("provider", "anthropic"),
            model=data.get("model", "claude-sonnet-4-20250514"),
            temperature=float(data.get("temperature", 0.7)),
            max_tokens=int(data.get("max_tokens", 8192)),
            openai_model=data.get("openai_model", "gpt-4o-mini"),
            anthropic_model=data.get("anthropic_model", "claude-sonnet-4-20250514"),
            max_iterations=int(data.get("max_iterations", 10)),
            max_tool_rounds=int(data.get("max_tool_rounds", 20)),
            max_retries=int(data.get("max_retries", 3)),
            validation_threshold=float(data.get("validation_threshold", 0.7)),
            system_prompt=data.get("system_prompt", ""),
            disabled_stages=disabled,
            artifacts=dict(data.get("artifacts", {})),
            stage_params=dict(data.get("stage_params", {})),
            active_strategies=dict(data.get("active_strategies", {})),
            capabilities=list(data.get("capabilities", [])),
            capability_params=dict(data.get("capability_params", {})),
            cost_budget_usd=float(data.get("cost_budget_usd", 10.0)),
            context_window=int(data.get("context_window", 200_000)),
            thinking_enabled=bool(data.get("thinking_enabled", False)),
            thinking_budget_tokens=int(data.get("thinking_budget_tokens", 10000)),
            preset=data.get("preset", ""),
        )

    @classmethod
    def from_json(cls, text: str) -> "HarnessConfig":
        """JSON 문자열 → HarnessConfig."""
        import json
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str) -> "HarnessConfig":
        """JSON 파일 → HarnessConfig."""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

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
            capabilities=list(harness_config.get("capabilities", []) or []),
            capability_params=dict(harness_config.get("capability_params", {}) or {}),
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
