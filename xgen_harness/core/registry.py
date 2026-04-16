"""
ArtifactRegistry — 스테이지 ↔ 구현체 매핑

artifact 시스템: stage_id → { artifact_name → StageClass }
프리셋에 따라 파이프라인 스테이지 목록을 빌드.

플러그인 확장:
  - entry_points(group="xgen_harness.stages") 로 외부 패키지에서 스테이지 자동 등록
  - register_stage() 공개 API 로 런타임 직접 등록
"""

import logging
import sys
from typing import Optional, Type

from .config import HarnessConfig, ALL_STAGES
from .stage import Stage, STAGE_DISPLAY_NAMES

logger = logging.getLogger("harness.registry")

# alias → stage_id 매핑
_ALIASES: dict[str, str] = {}
for sid in STAGE_DISPLAY_NAMES:
    # "s07_llm" → aliases: "llm", "LLM", "7"
    short = sid.split("_", 1)[1] if "_" in sid else sid
    num = sid[1:3] if sid.startswith("s") else ""
    _ALIASES[short] = sid
    _ALIASES[short.lower()] = sid
    _ALIASES[short.upper()] = sid
    _ALIASES[STAGE_DISPLAY_NAMES[sid]] = sid
    _ALIASES[STAGE_DISPLAY_NAMES[sid].lower()] = sid
    if num:
        _ALIASES[num] = sid
        _ALIASES[str(int(num))] = sid  # "07" → "7"


class ArtifactRegistry:
    """스테이지별 아티팩트(구현체) 레지스트리"""

    def __init__(self):
        # stage_id → { artifact_name → StageClass }
        self._registry: dict[str, dict[str, Type[Stage]]] = {}

    def register(self, stage_id: str, artifact_name: str, stage_class: Type[Stage]) -> None:
        if stage_id not in self._registry:
            self._registry[stage_id] = {}
        self._registry[stage_id][artifact_name] = stage_class
        logger.debug("Registered artifact: %s/%s", stage_id, artifact_name)

    def resolve_stage_id(self, key: str) -> str:
        """alias/display/numeric → stage_id 해석"""
        if key in self._registry:
            return key
        resolved = _ALIASES.get(key) or _ALIASES.get(key.lower())
        if resolved:
            return resolved
        raise KeyError(f"Unknown stage key: {key!r}")

    def get(self, stage_id: str, artifact_name: str = "default") -> Type[Stage]:
        """스테이지 클래스 반환"""
        artifacts = self._registry.get(stage_id)
        if not artifacts:
            raise KeyError(f"No artifacts registered for stage: {stage_id}")
        cls = artifacts.get(artifact_name)
        if not cls:
            if "default" in artifacts:
                logger.warning("Artifact %r not found for %s, using default", artifact_name, stage_id)
                return artifacts["default"]
            raise KeyError(f"Artifact {artifact_name!r} not found for stage {stage_id}")
        return cls

    def list_artifacts(self, stage_id: str) -> list[str]:
        return list(self._registry.get(stage_id, {}).keys())

    def list_stages(self) -> list[str]:
        return sorted(self._registry.keys())

    def build_pipeline_stages(self, config: HarnessConfig) -> list[Stage]:
        """설정에 따라 스테이지 인스턴스 목록 빌드"""
        active_ids = config.get_active_stage_ids()
        stages: list[Stage] = []

        for stage_id in active_ids:
            artifact_name = config.get_artifact_for_stage(stage_id)
            try:
                stage_cls = self.get(stage_id, artifact_name)
                stages.append(stage_cls())
            except KeyError as e:
                logger.error("Failed to build stage %s: %s", stage_id, e)
                continue

        stages.sort(key=lambda s: s.order)
        return stages

    def describe_all(self, config: Optional[HarnessConfig] = None) -> list[dict]:
        """모든 등록된 스테이지의 설명 목록 (API/UI용).

        stage_config.py의 설정 스키마도 포함하여,
        UI에서 스테이지 클릭 시 설정 필드를 렌더링할 수 있다.
        """
        from .stage_config import get_stage_config
        from .config import REQUIRED_STAGES

        active_ids = config.get_active_stage_ids() if config else list(self._registry.keys())
        descriptions = []
        for stage_id in sorted(self._registry.keys()):
            artifacts = self._registry[stage_id]
            default_cls = artifacts.get("default")
            if default_cls:
                desc = default_cls().describe()
                desc.active = stage_id in active_ids
                stage_cfg = get_stage_config(stage_id)
                descriptions.append({
                    "stage_id": desc.stage_id,
                    "display_name": desc.display_name,
                    "display_name_ko": desc.display_name_ko,
                    "phase": desc.phase,
                    "order": desc.order,
                    "active": desc.active,
                    "required": stage_id in REQUIRED_STAGES,
                    "artifacts": list(artifacts.keys()),
                    "strategies": [
                        {"name": s.name, "description": s.description, "is_default": s.is_default}
                        for s in desc.strategies
                    ],
                    # stage_config: UI 렌더링용 설정 스키마
                    "config": {
                        "description_ko": stage_cfg.get("description_ko", ""),
                        "description_en": stage_cfg.get("description_en", ""),
                        "fields": stage_cfg.get("fields", []),
                        "behavior": stage_cfg.get("behavior", []),
                    } if stage_cfg else None,
                })
        return sorted(descriptions, key=lambda d: d["order"])

    @classmethod
    def default(cls) -> "ArtifactRegistry":
        """기본 아티팩트가 등록된 레지스트리 생성"""
        registry = cls()
        _register_default_stages(registry)
        return registry


def _register_default_stages(registry: ArtifactRegistry) -> None:
    """모든 기본 스테이지를 레지스트리에 등록"""
    from ..stages.s01_input import InputStage
    from ..stages.s03_system_prompt import SystemPromptStage
    from ..stages.s07_llm import LLMStage
    from ..stages.s08_execute import ExecuteStage
    from ..stages.s10_decide import DecideStage
    from ..stages.s12_complete import CompleteStage

    registry.register("s01_input", "default", InputStage)
    registry.register("s03_system_prompt", "default", SystemPromptStage)
    registry.register("s07_llm", "default", LLMStage)
    registry.register("s08_execute", "default", ExecuteStage)
    registry.register("s10_decide", "default", DecideStage)
    registry.register("s12_complete", "default", CompleteStage)

    # Phase 2/3 스테이지 (구현되면 추가)
    try:
        from ..stages.s02_memory import MemoryStage
        registry.register("s02_memory", "default", MemoryStage)
    except ImportError:
        pass

    try:
        from ..stages.s04_tool_index import ToolIndexStage
        registry.register("s04_tool_index", "default", ToolIndexStage)
    except ImportError:
        pass

    try:
        from ..stages.s05_plan import PlanStage
        registry.register("s05_plan", "default", PlanStage)
    except ImportError:
        pass

    try:
        from ..stages.s06_context import ContextStage
        registry.register("s06_context", "default", ContextStage)
    except ImportError:
        pass

    try:
        from ..stages.s09_validate import ValidateStage
        registry.register("s09_validate", "default", ValidateStage)
    except ImportError:
        pass

    try:
        from ..stages.s11_save import SaveStage
        registry.register("s11_save", "default", SaveStage)
    except ImportError:
        pass

    # 플러그인 자동 탐색
    _discover_plugin_stages(registry)


def _discover_plugin_stages(registry: ArtifactRegistry) -> None:
    """Discover stages from installed packages via entry_points."""
    try:
        if sys.version_info >= (3, 10):
            from importlib.metadata import entry_points
            eps = entry_points(group="xgen_harness.stages")
        else:
            from importlib.metadata import entry_points
            eps = entry_points().get("xgen_harness.stages", [])

        for ep in eps:
            try:
                stage_class = ep.load()
                registry.register(ep.name, "default", stage_class)
                logger.info("Plugin stage registered: %s", ep.name)
            except Exception as e:
                logger.warning("Failed to load plugin stage %s: %s", ep.name, e)
    except Exception:
        pass  # No plugins installed


# ── 싱글턴 레지스트리 (기본 인스턴스) ──────────────────────────────
_DEFAULT_REGISTRY: Optional[ArtifactRegistry] = None


def _get_default_registry() -> ArtifactRegistry:
    """기본 레지스트리 싱글턴 반환 (lazy init)."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ArtifactRegistry.default()
    return _DEFAULT_REGISTRY


def register_stage(stage_id: str, artifact_name: str, stage_class: Type[Stage]) -> None:
    """외부 코드에서 스테이지를 직접 등록하는 공개 API.

    Usage::

        from xgen_harness.core.registry import register_stage
        register_stage("s99_custom", "default", MyCustomStage)
    """
    _get_default_registry().register(stage_id, artifact_name, stage_class)
    logger.info("Stage registered via public API: %s/%s", stage_id, artifact_name)
