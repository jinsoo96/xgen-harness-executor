"""
xgen-harness — XGEN 하네스 파이프라인 라이브러리

12-stage dual-abstraction agent pipeline.
xgen 생태계 전용 Pure Python 하네스 파이프라인.

Usage:
    from xgen_harness import Pipeline, PipelineState, HarnessConfig

    config = HarnessConfig(preset="standard", provider="anthropic")
    pipeline = Pipeline.from_config(config)
    state = PipelineState(user_input="Hello")
    result = await pipeline.run(state)
"""

from .core.pipeline import Pipeline
from .core.state import PipelineState, TokenUsage
from .core.config import HarnessConfig, ALL_STAGES, REQUIRED_STAGES
from .core.presets import PRESETS, Preset, get_preset, apply_preset, list_presets
from .core.stage import Stage, StageDescription, StrategyInfo
from .core.registry import ArtifactRegistry, register_stage
from .events.emitter import EventEmitter
from .events.types import (
    HarnessEvent,
    StageEnterEvent,
    StageExitEvent,
    MessageEvent,
    ToolCallEvent,
    ToolResultEvent,
    MetricsEvent,
    ErrorEvent,
    DoneEvent,
    MissingParamEvent,
)
from .errors import (
    HarnessError,
    ConfigError,
    ProviderError,
    ToolError,
    PipelineAbortError,
)
from .core.builder import PipelineBuilder
from .core.strategy_resolver import StrategyResolver
from .core.session import HarnessSession, SessionManager
from .orchestrator.dag import DAGOrchestrator, AgentNode, DAGEdge, DAGResult
from .orchestrator.multi_agent import MultiAgentExecutor
from .core.services import ServiceProvider, NullServiceProvider
from .tools.gallery import ToolPackageSpec, GalleryTool, load_tool_package, discover_gallery_tools
from .tools import ToolSource, register_tool_source, get_tool_sources
from .capabilities import (
    CapabilitySpec,
    CapabilityMatch,
    ParamSpec,
    ProviderKind,
    CapabilityRegistry,
    get_default_registry,
    set_default_registry,
    CapabilityMatcher,
    MatchStrategy,
    materialize_capabilities,
    merge_into_state,
    MaterializationReport,
    ParameterResolver,
    ResolveResult,
)

__version__ = "0.8.12"

__all__ = [
    # Core
    "Pipeline",
    "PipelineState",
    "TokenUsage",
    "HarnessConfig",
    "ALL_STAGES",
    "REQUIRED_STAGES",
    "PRESETS",
    "Preset",
    "get_preset",
    "apply_preset",
    "list_presets",
    "Stage",
    "StageDescription",
    "StrategyInfo",
    "ArtifactRegistry",
    "register_stage",
    # Events
    "EventEmitter",
    "HarnessEvent",
    "StageEnterEvent",
    "StageExitEvent",
    "MessageEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "MetricsEvent",
    "ErrorEvent",
    "DoneEvent",
    "MissingParamEvent",
    # Errors
    "HarnessError",
    "ConfigError",
    "ProviderError",
    "ToolError",
    "PipelineAbortError",
    # Builder & Session
    "PipelineBuilder",
    "HarnessSession",
    "SessionManager",
    # Orchestrator
    "DAGOrchestrator",
    "AgentNode",
    "DAGEdge",
    "DAGResult",
    "MultiAgentExecutor",
    # Services (pluggable)
    "ServiceProvider",
    "NullServiceProvider",
    # Gallery Tools
    "ToolPackageSpec",
    "GalleryTool",
    "load_tool_package",
    "discover_gallery_tools",
    # Plugin API
    "ToolSource",
    "register_tool_source",
    "get_tool_sources",
    "register_stage",
    # Capability System
    "CapabilitySpec",
    "CapabilityMatch",
    "ParamSpec",
    "ProviderKind",
    "CapabilityRegistry",
    "get_default_registry",
    "set_default_registry",
    "CapabilityMatcher",
    "MatchStrategy",
    "materialize_capabilities",
    "merge_into_state",
    "MaterializationReport",
    "ParameterResolver",
    "ResolveResult",
]
