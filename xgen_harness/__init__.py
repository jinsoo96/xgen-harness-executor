"""
xgen-harness — XGEN 하네스 파이프라인 라이브러리

12-stage dual-abstraction agent pipeline.
xgen 생태계 전용 Pure Python 하네스 파이프라인.

Usage:
    from xgen_harness import Pipeline, PipelineState, HarnessConfig

    # provider 생략 → providers.get_default_provider() 가 런타임 해석
    # (XGEN_HARNESS_DEFAULT_PROVIDER env → openai → anthropic → 레지스트리 첫 항목)
    config = HarnessConfig()
    pipeline = Pipeline.from_config(config)
    state = PipelineState(user_input="Hello")
    result = await pipeline.run(state)
"""

from .core.pipeline import Pipeline
from .core.state import PipelineState, TokenUsage
from .core.config import HarnessConfig, ALL_STAGES, REQUIRED_STAGES
from .core.presets import PRESETS, Preset, get_preset, apply_preset, list_presets
from .core.stage import Stage, StageDescription, StrategyInfo
from .core.stage_io import StageInput, StageOutput, STAGE_IO_SPECS, get_stage_io
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
    ServiceLookupEvent,
    CapabilityBindEvent,
    StageSubstepEvent,
    RetryEvent,
    PlanningEvent,
)
from .core.catalog import get_catalog
from .core.planner import HarnessPlanner, HarnessPlan
from .core.provider_bootstrap import ensure_provider
from .errors import (
    HarnessError,
    ConfigError,
    ProviderError,
    ToolError,
    PipelineAbortError,
    # v0.11.27 — 세분화된 예외도 top-level 노출. 외부 기여자가
    # `from xgen_harness import RateLimitError` 처럼 잡을 수 있도록.
    RateLimitError,
    OverloadError,
    ContextOverflowError,
    ToolTimeoutError,
    MCPConnectionError,
    ValidationError,
    ErrorCategory,
)
from .core.builder import PipelineBuilder
from .core.strategy_resolver import StrategyResolver
from .core.session import HarnessSession, SessionManager
from .orchestrator.dag import DAGOrchestrator, AgentNode, DAGEdge, DAGResult, DAGCycleError
from .orchestrator.multi_agent import MultiAgentExecutor
from .core.services import ServiceProvider, NullServiceProvider
from .tools.gallery import ToolPackageSpec, GalleryTool, load_tool_package, discover_gallery_tools
from .tools import ToolSource, register_tool_source, get_tool_sources
# v0.17.0 — Policy Gate: ABC/인프라만 노출. 구체 Guard 클래스(TokenBudgetGuard,
# ToolPreconditionGuard 등)는 top-level 에 두지 않는다 — 외부 코드가 이름에
# 결합되지 않도록. 구체 Guard 는 entry_points 로만 발견·사용.
from .stages.strategies.guard import (
    Guard,
    GuardResult,
    GuardChain,
    HookPoint,
    HookContext,
    FieldSchema as GuardFieldSchema,
    available_guards,
    register_guard,
    describe_guards,
    build_guard_chain,
)
from .adapters.resource_registry import (
    register_xgen_node_resolver,
    get_xgen_node_resolver,
    XgenNodeResolver,
)
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
from .compile import (
    compile_workflow as compile,
    compile_workflow,
    build_wheel,
    WheelBuildResult,
    WorkflowSnapshot,
    SNAPSHOT_VERSION,
    load_snapshot,
    ExternalInputSpec,
    InputType,
    scan_placeholders,
    merge_scanned,
    collect_runtime_values,
    MissingExternalInputError,
    DependencyResolver,
    resolve_dependencies,
    register_dependency_rule,
    DependencyRule,
    # 단계 5 — MCP 래퍼
    serve_mcp,
    run_mcp_blocking,
    MCPNotInstalledError,
    # 단계 6 — 갤러리 discover
    InstalledGallery,
    discover_galleries,
    get_gallery,
)
# v0.20.0 — Sandbox Verifier (Phase B: publish-time gate)
from .core.sandbox import Sandbox, SandboxLimits, SandboxResult, run_sandboxed
from .core.sandbox_verifiers import (
    SandboxVerifier,
    VerifyResult,
    MCPStdioVerifier,
    register_sandbox_verifier,
    get_sandbox_verifier,
    list_sandbox_verifiers,
    bootstrap_default_sandbox_verifiers,
    verify_mcp_stdio,
)

__version__ = "0.20.0"

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
    "StageInput",
    "StageOutput",
    "STAGE_IO_SPECS",
    "get_stage_io",
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
    "ServiceLookupEvent",
    "CapabilityBindEvent",
    "StageSubstepEvent",
    "RetryEvent",
    # v0.12.0 — Harness Planner (REAL_HARNESS §4)
    "PlanningEvent",
    "get_catalog",
    "HarnessPlanner",
    "HarnessPlan",
    "ensure_provider",
    # Errors
    "HarnessError",
    "ConfigError",
    "ProviderError",
    "ToolError",
    "PipelineAbortError",
    # v0.11.27 — 세분화된 예외 top-level export
    "RateLimitError",
    "OverloadError",
    "ContextOverflowError",
    "ToolTimeoutError",
    "MCPConnectionError",
    "ValidationError",
    "ErrorCategory",
    # Builder & Session
    "PipelineBuilder",
    "HarnessSession",
    "SessionManager",
    # Orchestrator
    "DAGOrchestrator",
    "AgentNode",
    "DAGEdge",
    "DAGResult",
    "DAGCycleError",
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
    # 호스트 독립성 — xgen 노드 resolver 외부 주입 (v0.11.24)
    "register_xgen_node_resolver",
    "get_xgen_node_resolver",
    "XgenNodeResolver",
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
    # Compiler (v0.10.0+)
    "compile",
    "compile_workflow",
    "build_wheel",
    "WheelBuildResult",
    "WorkflowSnapshot",
    "SNAPSHOT_VERSION",
    "load_snapshot",
    "ExternalInputSpec",
    "InputType",
    "scan_placeholders",
    "merge_scanned",
    "collect_runtime_values",
    "MissingExternalInputError",
    "DependencyResolver",
    "resolve_dependencies",
    "register_dependency_rule",
    "DependencyRule",
    "serve_mcp",
    "run_mcp_blocking",
    "MCPNotInstalledError",
    "InstalledGallery",
    "discover_galleries",
    "get_gallery",
    # v0.20.0 — Sandbox Verifier
    "Sandbox",
    "SandboxLimits",
    "SandboxResult",
    "run_sandboxed",
    "SandboxVerifier",
    "VerifyResult",
    "MCPStdioVerifier",
    "register_sandbox_verifier",
    "get_sandbox_verifier",
    "list_sandbox_verifiers",
    "bootstrap_default_sandbox_verifiers",
    "verify_mcp_stdio",
]
