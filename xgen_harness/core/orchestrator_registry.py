"""
Orchestrator Registry — LLM 이 선택 가능한 실행 패턴 레지스트리.

철학 (v0.15.0 재귀적 자율주행 / 자동 연동 자동 확장성):
  - `orchestrator_hint` 의 enum 을 리터럴로 박지 않는다.
  - 엔진 기본 5개는 defaults 로 등록하되, 외부 플러그인이 `register_orchestrator()`
    한 줄만 호출하면 즉시 Planner 의 도구 스키마(enum) 에 합류한다.
  - LLM 은 레지스트리의 description 을 보고 어느 패턴이 이번 요청에 어울릴지 판단.

이게 "하드코딩 없이 자동 연동" 의 구현 — planner.py / _build_plan_from_tool_input /
이식측 dispatcher / 프론트 PlanningCard 모두 이 레지스트리를 조회한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OrchestratorSpec:
    """Orchestrator 설명."""
    name: str
    description: str
    # 프로그램적 힌트 — 이식측 dispatcher 가 이 값을 보고 실제 구현 매핑.
    # 엔진은 해석하지 않음 (자유 문자열).
    dispatch_key: str = ""


_REGISTRY: dict[str, OrchestratorSpec] = {}
_DEFAULTS_REGISTERED = False


def register_orchestrator(
    name: str,
    *,
    description: str = "",
    dispatch_key: str = "",
) -> None:
    """새 orchestrator 를 레지스트리에 등록.

    외부 패키지는 import 시 이 함수를 호출하거나 `entry_points` 그룹
    `xgen_harness.orchestrators` 에 Spec 을 노출하면 자동 발견된다.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("orchestrator name must be non-empty string")
    _REGISTRY[name] = OrchestratorSpec(
        name=name, description=description, dispatch_key=dispatch_key or name
    )


def unregister_orchestrator(name: str) -> None:
    """테스트/런타임 교체용. 기본값도 제거 가능."""
    _REGISTRY.pop(name, None)


def list_orchestrators() -> list[str]:
    """등록된 orchestrator 이름 리스트 (정렬). `build_plan_tool()` 이 enum 으로 사용."""
    _ensure_defaults_registered()
    return sorted(_REGISTRY.keys())


def get_orchestrator_specs() -> list[dict]:
    """Planner / 프론트 / 이식측에 설명까지 포함해 노출."""
    _ensure_defaults_registered()
    return [
        {"name": spec.name, "description": spec.description, "dispatch_key": spec.dispatch_key}
        for spec in sorted(_REGISTRY.values(), key=lambda s: s.name)
    ]


def get_orchestrator(name: str) -> Optional[OrchestratorSpec]:
    _ensure_defaults_registered()
    return _REGISTRY.get(name)


def _ensure_defaults_registered() -> None:
    """엔진 기본 5개 패턴. 사용자가 `unregister_orchestrator` 로 덜어낼 수 있도록 idempotent."""
    global _DEFAULTS_REGISTERED
    if _DEFAULTS_REGISTERED:
        return
    _DEFAULTS_REGISTERED = True

    # 기본 5개 — geny-harness / LangGraph 패턴을 참고한 최소 집합.
    # 외부에서 새 패턴 추가는 `register_orchestrator("custom_name", ...)` 한 줄.
    if "linear" not in _REGISTRY:
        register_orchestrator(
            "linear",
            description="한 번 돌고 종료. 단순 Q&A, 단발성 요청에 적합.",
            dispatch_key="linear",
        )
    if "iterative" not in _REGISTRY:
        register_orchestrator(
            "iterative",
            description="매 iter 재계획. Plan 이 이전 결과 보고 다음 행동 결정.",
            dispatch_key="iterative",
        )
    if "react" not in _REGISTRY:
        register_orchestrator(
            "react",
            description="도구 호출 결과(Observation)보고 다음 Thought→Action.",
            dispatch_key="react",
        )
    if "plan_execute" not in _REGISTRY:
        register_orchestrator(
            "plan_execute",
            description="첫 Plan 고수. 멀티 스텝 계획을 세우고 흔들리지 않고 실행.",
            dispatch_key="plan_execute",
        )
    if "dag" not in _REGISTRY:
        register_orchestrator(
            "dag",
            description="멀티 에이전트 DAG. 노드 간 병렬/직렬 혼합, 이식측 DAGOrchestrator 가 실행.",
            dispatch_key="dag",
        )

    # entry_points 기반 자동 발견 — pip install xxx 한 것이 즉시 합류.
    _discover_from_entry_points()


def _discover_from_entry_points() -> None:
    """외부 패키지가 `xgen_harness.orchestrators` 그룹에 Spec 을 노출하면 자동 등록."""
    try:
        from importlib.metadata import entry_points
    except Exception:
        return
    try:
        eps = entry_points()
        group = "xgen_harness.orchestrators"
        if hasattr(eps, "select"):
            items = eps.select(group=group)  # Python 3.10+
        else:
            items = eps.get(group, [])       # legacy
        for ep in items:
            try:
                factory = ep.load()
                spec = factory() if callable(factory) else factory
                if isinstance(spec, OrchestratorSpec):
                    _REGISTRY[spec.name] = spec
                elif isinstance(spec, dict) and spec.get("name"):
                    register_orchestrator(
                        spec["name"],
                        description=spec.get("description", ""),
                        dispatch_key=spec.get("dispatch_key", ""),
                    )
            except Exception:
                # 개별 entry_point 실패는 전체 등록을 막지 않는다.
                continue
    except Exception:
        return
