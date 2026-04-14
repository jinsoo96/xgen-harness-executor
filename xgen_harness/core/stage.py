"""
Stage ABC — 모든 스테이지의 기반 인터페이스

Stage×Strategy 이중 추상화 기반 인터페이스.
Dual Abstraction:
  Level 1: Stage 전체 교체 (artifact)
  Level 2: Stage 내부 Strategy 교체
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .state import PipelineState


# --- 표시 이름 매핑 (사용자 편의 용어 강제) ---

STAGE_DISPLAY_NAMES: dict[str, str] = {
    "s01_input": "Input",
    "s02_memory": "Memory",
    "s03_system_prompt": "System Prompt",
    "s04_tool_index": "Tool Index",
    "s05_plan": "Plan",
    "s06_context": "Context",
    "s07_llm": "LLM",
    "s08_execute": "Execute",
    "s09_validate": "Validate",
    "s10_decide": "Decide",
    "s11_save": "Save",
    "s12_complete": "Complete",
}

STAGE_DISPLAY_NAMES_KO: dict[str, str] = {
    "s01_input": "입력",
    "s02_memory": "기억",
    "s03_system_prompt": "시스템 프롬프트",
    "s04_tool_index": "도구 색인",
    "s05_plan": "계획",
    "s06_context": "컨텍스트",
    "s07_llm": "LLM 호출",
    "s08_execute": "도구 실행",
    "s09_validate": "검증",
    "s10_decide": "판단",
    "s11_save": "저장",
    "s12_complete": "완료",
}


@dataclass
class StrategyInfo:
    """스테이지 내부 전략 정보"""
    name: str
    description: str
    is_default: bool = False


@dataclass
class StageDescription:
    """스테이지 설명 (UI/API용)"""
    stage_id: str
    display_name: str
    display_name_ko: str
    phase: str                          # "ingress" | "loop" | "egress"
    order: int
    description: str = ""
    required: bool = False
    active: bool = True
    strategies: list[StrategyInfo] = field(default_factory=list)
    # I/O 계약 (Stage 인터페이스 정형화)
    input_requires: list[str] = field(default_factory=list)
    input_optional: list[str] = field(default_factory=list)
    output_produces: list[str] = field(default_factory=list)
    output_modifies: list[str] = field(default_factory=list)


class Stage(ABC):
    """하네스 스테이지 기반 클래스

    Stage×Strategy 이중 추상화:
      Level 1: Stage 전체 교체 (Artifact) — 같은 stage_id, 다른 구현
      Level 2: Stage 내부 Strategy 교체 — 같은 Stage, 다른 전략

    I/O 계약:
      각 Stage는 input_spec/output_spec으로 뭘 받고 뭘 내보내는지 선언.
      Pipeline이 실행 전에 input 검증, 실행 후에 output 추적.
    """

    @property
    @abstractmethod
    def stage_id(self) -> str:
        """내부 ID (예: 's07_llm')"""
        ...

    @property
    @abstractmethod
    def order(self) -> int:
        """실행 순서 (1~12)"""
        ...

    @property
    def input_spec(self):
        """이 스테이지가 요구하는 입력. override 가능."""
        from .stage_io import get_stage_io
        spec = get_stage_io(self.stage_id)
        return spec.get("input")

    @property
    def output_spec(self):
        """이 스테이지가 생산하는 출력. override 가능."""
        from .stage_io import get_stage_io
        spec = get_stage_io(self.stage_id)
        return spec.get("output")

    @property
    def display_name(self) -> str:
        return STAGE_DISPLAY_NAMES.get(self.stage_id, self.stage_id)

    @property
    def display_name_ko(self) -> str:
        return STAGE_DISPLAY_NAMES_KO.get(self.stage_id, self.display_name)

    @property
    def phase(self) -> str:
        if self.order <= 4:
            return "ingress"
        elif self.order <= 10:
            return "loop"
        else:
            return "egress"

    @abstractmethod
    async def execute(self, state: "PipelineState") -> dict:
        """스테이지 실행. state를 직접 변경하고 결과 dict를 반환."""
        ...

    def should_bypass(self, state: "PipelineState") -> bool:
        """이 스테이지를 건너뛸지 결정"""
        return False

    async def on_enter(self, state: "PipelineState") -> None:
        """스테이지 시작 전 훅"""
        pass

    async def on_exit(self, result: dict, state: "PipelineState") -> None:
        """스테이지 완료 후 훅"""
        pass

    async def on_error(self, error: Exception, state: "PipelineState") -> Optional[dict]:
        """에러 발생 시 훅. dict 반환 시 복구, None 반환 시 전파."""
        return None

    def describe(self) -> StageDescription:
        desc = StageDescription(
            stage_id=self.stage_id,
            display_name=self.display_name,
            display_name_ko=self.display_name_ko,
            phase=self.phase,
            order=self.order,
            strategies=self.list_strategies(),
        )
        # I/O 스펙 포함
        if self.input_spec:
            desc.input_requires = self.input_spec.requires
            desc.input_optional = self.input_spec.optional
        if self.output_spec:
            desc.output_produces = self.output_spec.produces
            desc.output_modifies = self.output_spec.modifies
        return desc

    def get_param(self, key: str, state: "PipelineState", default=None):
        """스테이지 파라미터 조회.

        조회 순서:
        1. state.config.stage_params[stage_id][key]  (사용자가 UI에서 설정한 값)
        2. default 인자                                (코드에서 넘긴 값, 보통 config.xxx)
        3. stage_config.py 기본값                      (개발자가 정의한 스키마 기본값)

        주의: stage_config 기본값이 코드 폴백보다 우선하면
        config에 설정된 값을 덮어씌우는 버그가 발생.
        (예: config.provider="openai"인데 stage_config.default="anthropic"이 반환)
        """
        # 1. 사용자 설정 (런타임 — UI에서 명시 설정)
        if hasattr(state, "config") and state.config:
            params = state.config.stage_params.get(self.stage_id, {})
            if key in params:
                return params[key]

        # 2. 코드 폴백 (config.provider 등 — 호출자가 넘긴 값)
        if default is not None:
            return default

        # 3. stage_config.py 스키마 기본값 (최후 폴백)
        from .stage_config import get_stage_config
        cfg = get_stage_config(self.stage_id)
        for f in cfg.get("fields", []):
            if f.get("id") == key:
                return f.get("default")

        return None

    def resolve_strategy(self, slot_name: str, state: "PipelineState", default_impl: str = ""):
        """Strategy 이름을 인스턴스로 해석.

        조회 순서:
        1. state.config.active_strategies[stage_id]  (UI에서 선택한 전략)
        2. state.config.stage_params[stage_id]["strategy"]
        3. default_impl 인자

        geny-harness의 Stage×Strategy 이중 추상화와 동일 패턴.
        """
        from .strategy_resolver import StrategyResolver

        # 사용자가 선택한 전략 이름 조회
        impl_name = default_impl
        if hasattr(state, "config") and state.config:
            # active_strategies에서 먼저 (UI 드롭다운)
            active = getattr(state.config, "active_strategies", {})
            if self.stage_id in active:
                impl_name = active[self.stage_id]
            # stage_params에서 폴백
            params = state.config.stage_params.get(self.stage_id, {})
            if "strategy" in params:
                impl_name = params["strategy"]
            # slot별 파라미터
            if slot_name in params:
                impl_name = params[slot_name]

        if not impl_name:
            return None

        resolver = StrategyResolver.default()
        strategy_config = {}
        if hasattr(state, "config") and state.config:
            strategy_config = state.config.stage_params.get(self.stage_id, {})

        return resolver.resolve(self.stage_id, slot_name, impl_name, strategy_config)

    def list_strategies(self) -> list[StrategyInfo]:
        return []
