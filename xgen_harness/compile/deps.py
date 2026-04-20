"""
Dependency Resolver — 컴파일 산출 wheel 의 pip 의존성 자동 계산.

엔진은 룰 레지스트리만 가진다. 외부 패키지는 `register_dependency_rule()` 로
자기가 제공하는 ProviderKind / provider_ref / 노드 카테고리에 대해 추가 의존성
선언 가능. 하드코딩 없이 새 provider/capability/node 가 자동 반영된다.

기본 제공 규칙 (빌트인) 도 전부 레지스트리 항목으로 등록되어 있어 외부에서
override 가능하다. 신규 공급자가 추가될 때 엔진 소스 수정이 필요 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .snapshot import WorkflowSnapshot


# ──────────────────────────────────────────────────────────────
# 빌트인 버전 floor — 외부에서 import 해 override 가능.
# 엔진 코드 박제 대신 상수로 노출하여 패키징 정책이 바뀌어도 한 곳만 수정.
# ──────────────────────────────────────────────────────────────

MCP_MIN_VERSION: str = ">=0.9"              # mcp 서버 SDK 최소 버전
QDRANT_MIN_VERSION: str = ">=1.7"           # qdrant-client 최소 버전


# ──────────────────────────────────────────────────────────────
# Rule 레지스트리
# ──────────────────────────────────────────────────────────────

# 룰: snapshot → Iterable[(pkg, version_spec)]
RuleFn = Callable[[WorkflowSnapshot], Iterable[tuple[str, str]]]


@dataclass
class DependencyRule:
    name: str
    fn: RuleFn
    description: str = ""


_RULES: dict[str, DependencyRule] = {}


def register_dependency_rule(
    name: str,
    fn: RuleFn,
    *,
    description: str = "",
    overwrite: bool = False,
) -> None:
    """외부 패키지가 자기 패키지 의존성을 자동 주입할 때 호출.

    예::

        register_dependency_rule(
            "my_vendor.vision",
            lambda snap: [("my-vendor-vision", ">=1.2")]
                         if "my_vendor.vision_search" in snap.harness_config.get("capabilities", []) else [],
            description="vision capability → my-vendor-vision",
        )
    """
    if name in _RULES and not overwrite:
        return
    _RULES[name] = DependencyRule(name=name, fn=fn, description=description)


def unregister_dependency_rule(name: str) -> bool:
    return _RULES.pop(name, None) is not None


def list_dependency_rules() -> list[str]:
    return sorted(_RULES.keys())


# ──────────────────────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────────────────────

class DependencyResolver:
    """룰 기반 pip 의존성 해석기. 외부 의존성 표로 집계."""

    def __init__(self) -> None:
        _ensure_builtin_rules()

    def resolve(self, snapshot: WorkflowSnapshot) -> dict[str, str]:
        """snapshot → {pkg: version_spec}.

        - 최종 결과는 package 이름 기준 중복 제거. 같은 pkg 에 여러 버전 요구가
          들어오면 첫 등장의 spec 을 유지하고 나머지는 병합(합집합 구분자 ``,``)
          하지 않음 — 사용자가 snapshot.dependencies 에서 명시적으로 덮어쓸 수 있음.
        - snapshot.dependencies 의 명시값이 항상 최우선.
        """
        _ensure_builtin_rules()
        result: dict[str, str] = {}

        for rule in _RULES.values():
            try:
                for pkg, ver in rule.fn(snapshot) or ():
                    if not pkg:
                        continue
                    result.setdefault(pkg, ver or "")
            except Exception:
                # 룰 실패가 전체 컴파일을 막지 않도록 방어적 (외부 룰 대응).
                continue

        # snapshot.dependencies 가 있으면 최우선 override.
        for pkg, ver in (snapshot.dependencies or {}).items():
            if pkg:
                result[pkg] = ver or ""
        return result


def resolve_dependencies(snapshot: WorkflowSnapshot) -> dict[str, str]:
    """단일 함수 helper — snapshot → {pkg: version_spec}."""
    return DependencyResolver().resolve(snapshot)


# ──────────────────────────────────────────────────────────────
# 빌트인 규칙 — 엔진 자체의 기본값. 전부 레지스트리 항목으로.
# ──────────────────────────────────────────────────────────────

_BUILTIN_DONE = False


def _ensure_builtin_rules() -> None:
    global _BUILTIN_DONE
    if _BUILTIN_DONE:
        return
    _BUILTIN_DONE = True

    register_dependency_rule("xgen_harness.core", _rule_xgen_harness,
                             description="xgen-harness core (항상)")
    register_dependency_rule("xgen_harness.provider", _rule_provider_pkg,
                             description="provider 에 필요한 SDK (레지스트리 경유)")
    register_dependency_rule("xgen_harness.mcp", _rule_mcp,
                             description="mcp_sessions 존재 시 MCP 클라이언트")
    register_dependency_rule("xgen_harness.rag", _rule_rag,
                             description="rag_collections 존재 시 vector store 클라이언트")
    register_dependency_rule("xgen_harness.capability", _rule_capability_extras,
                             description="CapabilitySpec.metadata['extras_package'] 에 선언된 추가 의존성")


def _rule_xgen_harness(snapshot: WorkflowSnapshot) -> list[tuple[str, str]]:
    spec = snapshot.harness_version or ""
    return [("xgen-harness", spec)]


def _rule_provider_pkg(snapshot: WorkflowSnapshot) -> list[tuple[str, str]]:
    """provider 가 SDK 의존을 가지면 선언. 레지스트리(xgen_harness.providers) 조회.

    builtin provider 는 httpx 만으로 동작하므로 추가 pip dep 없음. 외부 provider 가
    ``register_provider(name, cls, sdk_requirement=...)`` 식으로 선언한 경우를 대비해
    ``PROVIDER_SDK_REQUIREMENTS`` (선택) 를 조회한다.
    """
    provider = (snapshot.harness_config or {}).get("provider") or ""
    if not provider:
        return []
    try:
        from .. import providers as _providers
    except Exception:
        return []
    table = getattr(_providers, "PROVIDER_SDK_REQUIREMENTS", None)
    if not isinstance(table, dict):
        return []
    entry = table.get(provider.lower())
    if not entry:
        return []
    # entry 는 (pkg, version_spec) 또는 dict.
    if isinstance(entry, tuple) and len(entry) == 2:
        return [entry]
    if isinstance(entry, dict) and entry.get("pkg"):
        return [(entry["pkg"], entry.get("version", ""))]
    return []


def _rule_mcp(snapshot: WorkflowSnapshot) -> list[tuple[str, str]]:
    cfg = snapshot.harness_config or {}
    wf = snapshot.workflow_data or {}
    if cfg.get("mcp_sessions") or wf.get("mcp_sessions"):
        return [("mcp", MCP_MIN_VERSION)]
    # capability_params 에 mcp 언급이 있어도 true.
    caps = cfg.get("capability_params") or {}
    if any("mcp" in str(k).lower() for k in caps):
        return [("mcp", MCP_MIN_VERSION)]
    return []


def _rule_rag(snapshot: WorkflowSnapshot) -> list[tuple[str, str]]:
    cfg = snapshot.harness_config or {}
    wf = snapshot.workflow_data or {}
    if cfg.get("rag_collections") or wf.get("rag_collections"):
        return [("qdrant-client", QDRANT_MIN_VERSION)]
    return []


def _rule_capability_extras(snapshot: WorkflowSnapshot) -> list[tuple[str, str]]:
    """CapabilitySpec.metadata['extras_package'] 에 선언된 외부 패키지 수집.

    외부 기여자가 CapabilitySpec 을 등록하며 자기 패키지도 같이 넣을 수 있도록.
    """
    cfg = snapshot.harness_config or {}
    declared = cfg.get("capabilities") or []
    if not declared:
        return []
    try:
        from ..capabilities import get_default_registry
        reg = get_default_registry()
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in declared:
        try:
            spec = reg.get(name)
        except Exception:
            spec = None
        if not spec:
            continue
        extras = getattr(spec, "metadata", None)
        if not isinstance(extras, dict):
            continue
        pkg = extras.get("extras_package")
        ver = extras.get("extras_version", "")
        if pkg and pkg not in seen:
            out.append((str(pkg), str(ver)))
            seen.add(pkg)
    return out
