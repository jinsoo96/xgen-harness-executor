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
from .core.registry import ArtifactRegistry
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

__version__ = "0.1.0"

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
]
