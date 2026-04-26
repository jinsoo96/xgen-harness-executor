"""
HarnessConfig — 파이프라인 설정

프리셋 없음. v0.14.0 에서 s07_llm 삭제 + 번호 시프트로 11개 스테이지 (s00_harness 제외).
s00_harness 가 Provider/Strategy/본문호출/Planner 를 모두 통제 (재귀적 자율주행).
workflow_data.harness_config에서 로드.
"""

import logging
from dataclasses import dataclass, field, fields
from typing import Any, Optional

from .stage_config import canonical_stage_id as _canonical, canonical_stage_id_map as _alias_map

_cfg_logger = logging.getLogger("harness.core.config")


# 전체 11 스테이지 (기본 전부 활성, s00_harness 는 별도 통제탑)
# v0.14.0: s07_llm 삭제 + 번호 시프트. s00_harness 가 본문 LLM 호출 소유.
# v0.17.0: 이 리스트는 "원래부터 있던 기본 Stage" 화이트리스트. 새 Stage
# (예: s05_policy Policy Gate) 는 여기 박지 않고 registry 에 등록만 —
# `get_active_stage_ids()` 가 registry 와 병합해서 런타임 목록 생성.
ALL_STAGES = [
    "s01_input",
    "s02_history",
    "s03_prompt",
    "s04_tool",
    "s05_strategy",
    "s06_context",
    "s07_act",
    "s08_judge",
    "s09_decide",
    "s10_save",
    "s11_finalize",
]

# 비활성화 불가 스테이지 — 엔진 기본값 3개. 외부 기여자는 `mark_stage_required()` 로 추가.
# v0.22.0 — 하드 set 이었던 것을 live set 으로 유지 + 등록 API 추가. 모든 참조는 이 동일
# set 을 읽기 때문에 외부 등록이 즉시 반영된다 (snapshot 이 아니다).
REQUIRED_STAGES: set[str] = {"s01_input", "s09_decide", "s11_finalize"}


def mark_stage_required(stage_id: str) -> None:
    """새 Stage 를 "비활성화 불가" 로 등록.

    외부 패키지가 자기 Stage 를 반드시 돌게 하려면 import 시 호출.
    기본 3개는 엔진이 이미 등록돼 있다.
    """
    if not isinstance(stage_id, str) or not stage_id.strip():
        raise ValueError("stage_id must be non-empty string")
    REQUIRED_STAGES.add(stage_id)


def unmark_stage_required(stage_id: str) -> None:
    """테스트/운영에서 필수 지정 해제."""
    REQUIRED_STAGES.discard(stage_id)


def get_required_stages() -> set[str]:
    """현재 필수 Stage id set 의 읽기용 스냅샷 복사."""
    return set(REQUIRED_STAGES)


@dataclass
class HarnessConfig:
    """하네스 파이프라인 설정 — 프리셋 없음, 스테이지 개별 토글"""

    # --- LLM ---
    # provider 기본값은 providers.get_default_provider() 가 런타임에 해석.
    # ("" / None / 누락 → XGEN_HARNESS_DEFAULT_PROVIDER env → openai → anthropic → 레지스트리 첫 항목)
    # model 기본값은 providers.PROVIDER_DEFAULT_MODEL[provider] 에서 런타임 해석.
    # "" 로 들어오면 어댑터/s01_input 이 Redis → env → PROVIDER_DEFAULT_MODEL 순으로 결정.
    provider: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192
    # 보조 LLM 호출 (s06 compaction / s08 judge / evaluation strategy 등) 용 max_tokens.
    # 본문(s07_act) 호출 max_tokens 와 분리 — 보조 호출은 짧은 판정/요약이라 작은 값으로
    # 비용 / 지연 관리. v0.26.11 — 매직넘버 500 4건 통합 (auxiliary_max_tokens 필드화).
    aux_max_tokens: int = 500

    # --- 폴백 모델 (provider 별) — PROVIDER_DEFAULT_MODEL 레지스트리가 단일 진실 소스.
    # "" 로 두면 런타임에 레지스트리 lookup. 새 provider 추가 시 레지스트리만 갱신.
    openai_model: str = ""
    anthropic_model: str = ""

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

    # --- Strategy 커스텀 변형 (디폴트 건드리지 않고 파라미터만 덮어쓴 복사본) ---
    # 외부 작업자가 "progressive_3level" 을 건드리지 않고 "progressive_v2" 라는
    # 복사본을 만들어 쓸 수 있게. active_strategies[stage_id] 가 variant 이름이면
    # base impl 의 클래스로 인스턴스 생성 후 params 를 configure 에 병합.
    #
    # 형식: {stage_id: [{"name": "progressive_v2",       # active_strategies 에서 참조할 이름
    #                    "base": "progressive_3level",    # 복제 원본 impl_name
    #                    "params": {"threshold": 20},     # configure 에 주입할 오버라이드
    #                    "label": "내 커스텀"              # UI 표시용 (옵션)
    #                   }]}
    strategy_variants: dict = field(default_factory=dict)

    # --- Capability 선언 (capability name 리스트, s04_tool가 자동 바인딩) ---
    capabilities: list = field(default_factory=list)       # ["retrieval.web_search", ...]
    capability_params: dict = field(default_factory=dict)  # capability_name → {param_id: value}

    # --- 외부 입력 선언 (컴파일 타겟) ---
    # 컴파일된 wheel 이 런타임에 요구하는 외부 값 계약. UI 가 이 선언을 보고
    # 배포 전 입력 폼을 자동 렌더. 컴파일러(xgen_harness.compile) 가 auto-scan 으로
    # ${VAR} 플레이스홀더를 발견하면 이 필드에 후보로 등록.
    #
    # 형식: {name: {"type": "secret"|"url"|"string"|"int"|"bool",
    #              "required": bool, "default": Any, "description": str}}
    external_inputs: dict = field(default_factory=dict)

    # --- 기타 ---
    cost_budget_usd: float = 10.0
    context_window: int = 200_000
    thinking_enabled: bool = False
    thinking_budget_tokens: int = 10000

    # --- 관찰/디버깅 ---
    # True 면 Pipeline/Stage 내부 세밀한 이벤트(ServiceLookup / CapabilityBind /
    # StageSubstep / Retry) 가 추가 발행. 기본 False 라 기존 SSE 출력량 변화 없음.
    verbose_events: bool = False

    # --- Harness Planner (v0.12.0 → v0.14.0 확장) ---
    # use_planner: 레거시 bool. True 면 s00_harness 가 ingress 최상단에 주입됨.
    # v0.14.0: s00_harness 가 본문 LLM 호출을 소유하므로 사실상 항상 True 가
    # 권장되는 상태. harness_mode 로 세분화:
    #   - "autonomous": Planner LLM 이 카탈로그 보고 Stage/Strategy/파라미터 자율 조립
    #   - "selected":   Planner LLM skip, 사용자 핀(pinned_chosen/strategies/params) 그대로
    #   - "off":        전체 Stage 실행 (레거시 noop 동작과 동일)
    # 빈 문자열이면 use_planner=True → "autonomous", False → "off" 로 해석.
    use_planner: bool = False
    harness_mode: str = ""

    # 레거시 호환
    preset: str = ""

    def __post_init__(self) -> None:
        """빈 provider 는 레지스트리 기반 기본값. harness_mode 미지정 시 use_planner 에서 파생."""
        if not self.provider:
            from ..providers import get_default_provider
            self.provider = get_default_provider()
        if not self.harness_mode:
            self.harness_mode = "autonomous" if self.use_planner else "off"

    # v0.25.3 — harness_mode 리터럴 비교를 쓰는 Stage / Strategy 가 늘어나면서
    # 문자열을 여기저기 하드코딩하면 새 모드(예: "safe_mode") 도입 시 추적 범위가 커짐.
    # HarnessConfig 에 헬퍼 3 개를 박제해서 도메인 언어를 캡슐화.
    def is_autonomous(self) -> bool:
        return str(self.harness_mode or "").lower() == "autonomous"

    def is_selected(self) -> bool:
        return str(self.harness_mode or "").lower() == "selected"

    def is_off(self) -> bool:
        return str(self.harness_mode or "").lower() == "off"

    def get_active_stage_ids(self) -> list[str]:
        """활성 스테이지 ID 목록.

        v0.17.0 — `ALL_STAGES` 하드 리스트 + registry 병합. registry 에 등록된
        Stage 클래스가 `default_active` 프로퍼티로 True 를 반환하면 기본 활성
        취급. `disabled_stages` 는 양쪽 모두에 적용.
        """
        ids = [s for s in ALL_STAGES if s not in self.disabled_stages]

        # registry 에 있지만 ALL_STAGES 에 없는 Stage 중 default_active=True 만 합류.
        try:
            from .registry import _get_default_registry
            reg = _get_default_registry()
            for sid in reg.list_stages():
                if sid in ids or sid in self.disabled_stages:
                    continue
                try:
                    cls = reg.get(sid, "default")
                    if getattr(cls, "default_active", True):
                        ids.append(sid)
                except Exception:
                    continue
        except Exception:
            pass

        return ids

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

        하드코딩된 필드 나열 없이 `dataclasses.fields(self)` 로 자동 발견.
        새 필드 추가해도 자동 포함됨 (허브 정신 유지).

        - set 은 정렬된 list 로 변환
        - tuple 은 list
        - 중첩 dict/list 는 그대로 (JSON-safe 가정)
        """
        data: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, set):
                value = sorted(value)
            elif isinstance(value, tuple):
                value = list(value)
            data[f.name] = value
        data["_schema_version"] = 1
        return data

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
        """dict → HarnessConfig 역직렬화 (자동 필드 매핑).

        `dataclasses.fields(cls)` 를 순회해 선언된 필드만 통과. 알 수 없는
        키는 무시(forward-compat). set 타입 필드는 list→set 변환.
        """
        declared = {f.name: f for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for name, f in declared.items():
            if name not in data:
                continue
            value = data[name]
            # set 필드는 list → set 복원 (JSON 에선 list 로 저장됨)
            if f.default_factory is set:
                value = set(value) if value is not None else set()
                if name == "disabled_stages":
                    value = value - REQUIRED_STAGES
            kwargs[name] = value
        return cls(**kwargs)

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

        # 비활성 스테이지 — 구 stage_id 도 canonical 로 정규화 (v0.11 alias)
        disabled = set()
        disabled_list = harness_config.get("disabled_stages", [])
        if isinstance(disabled_list, list):
            disabled = {_canonical(s) for s in disabled_list} - REQUIRED_STAGES

        # 레거시 preset 호환
        preset = harness_config.get("preset", "")
        if preset and not disabled_list:
            # 프리셋이 있으면 무시 (전부 활성)
            pass

        from ..providers import get_default_provider
        return cls(
            provider=(
                harness_config.get("provider")
                or agent_config.get("provider")
                or get_default_provider()
            ),
            # model 은 sentinel "" 로 받음 — 어댑터/s01_input 이 런타임에 PROVIDER_DEFAULT_MODEL 참조.
            model=harness_config.get("model") or agent_config.get("model", ""),
            temperature=float(harness_config.get("temperature", agent_config.get("temperature", 0.7))),
            max_tokens=int(harness_config.get("max_tokens", 8192)),
            # provider 별 폴백은 런타임 PROVIDER_DEFAULT_MODEL 에서 해석. 여기선 명시값만 전달.
            openai_model=harness_config.get("openai_model") or agent_config.get("openai_model", ""),
            anthropic_model=harness_config.get("anthropic_model") or agent_config.get("anthropic_model", ""),
            system_prompt=harness_config.get("system_prompt") or agent_config.get("system_prompt", ""),
            max_iterations=int(harness_config.get("max_iterations", 10)),
            max_retries=int(harness_config.get("max_retries", 3)),
            validation_threshold=float(harness_config.get("validation_threshold", 0.7)),
            disabled_stages=disabled,
            artifacts=_alias_map(harness_config.get("artifacts", {})),
            stage_params=_alias_map(harness_config.get("stage_params", {})),
            active_strategies=_alias_map(harness_config.get("active_strategies", {})),
            strategy_variants=_alias_map(dict(harness_config.get("strategy_variants", {}) or {})),
            thinking_enabled=bool(harness_config.get("thinking_enabled", False)),
            thinking_budget_tokens=int(harness_config.get("thinking_budget_tokens", 10000)),
            use_planner=bool(harness_config.get("use_planner", False)),
            harness_mode=str(harness_config.get("harness_mode", "") or ""),
            # v0.11.21 — top-level context_window 전파 (파싱 실패 시 dataclass 기본값 200_000 유지)
            context_window=_safe_int(
                harness_config.get("context_window"), default=200_000, minimum=1024,
            ),
            capabilities=list(harness_config.get("capabilities", []) or []),
            capability_params=dict(harness_config.get("capability_params", {}) or {}),
            external_inputs=dict(harness_config.get("external_inputs", {}) or {}),
            preset=preset,
        )


def _safe_int(value: Any, *, default: int, minimum: int | None = None) -> int:
    """빈 문자열/None/파싱 실패 시 default. minimum 미달 시 default."""
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and parsed < minimum:
        return default
    return parsed


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
            # 프로바이더별 default 모델 조회 — 레지스트리(providers/__init__.py)에서 해석.
            # 새 프로바이더 추가 시 config.py 수정 불필요.
            try:
                from ..providers import get_default_model
                per_provider_key = f"{provider}_model"
                model = _get(per_provider_key) or get_default_model(provider)
            except Exception as e:
                _cfg_logger.debug("default model resolution failed (provider=%s): %s", provider, e)
                model = ""

        # 프로바이더별 폴백 모델 (하드코딩 제거) — 레지스트리 기반
        try:
            from ..providers import get_default_model, list_providers
            per_provider_defaults = {
                f"{p}_model": _get(f"{p}_model") or get_default_model(p)
                for p in list_providers()
            }
        except Exception as e:
            _cfg_logger.debug("providers registry unavailable, per_provider_defaults 비움: %s", e)
            per_provider_defaults = {}

        return {
            "provider": provider,
            "model": model,
            **per_provider_defaults,
            "system_prompt": _get("system_prompt"),
            "temperature": _get("temperature") or "0.7",
        }
    return {}
