"""
xgen-harness 구조 검증 테스트

API 호출 없이 구조만 검증:
- 모듈 임포트 (v0.3.0 전체)
- 설정 생성 + 프리셋
- 프로바이더 레지스트리
- ServiceProvider
- Gallery Tool 시스템
- 레지스트리 + 파이프라인 빌드
- 이벤트 변환
- PipelineState
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


def test_imports():
    """v0.3.0 전체 import 확인"""
    from xgen_harness import (
        Pipeline, PipelineState, HarnessConfig,
        PRESETS, Preset, get_preset, apply_preset, list_presets,
        Stage, StageDescription, StrategyInfo,
        ArtifactRegistry, EventEmitter,
        HarnessError, ConfigError, ProviderError,
        PipelineBuilder, HarnessSession, SessionManager,
        DAGOrchestrator, AgentNode, DAGEdge, DAGResult, MultiAgentExecutor,
        ServiceProvider, NullServiceProvider,
        ToolPackageSpec, GalleryTool, load_tool_package, discover_gallery_tools,
    )
    from xgen_harness.providers.anthropic import AnthropicProvider
    from xgen_harness.providers.openai import OpenAIProvider
    from xgen_harness.providers import (
        create_provider, register_provider, list_providers,
        get_api_key_env, PROVIDER_API_KEY_MAP, PROVIDER_DEFAULT_MODEL,
    )
    from xgen_harness.events import event_to_dict, StageEnterEvent, DoneEvent
    from xgen_harness.integrations.xgen_streaming import convert_to_xgen_event
    from xgen_harness.integrations.xgen_services import XgenServiceProvider
    from xgen_harness.tools.base import Tool, ToolResult
    from xgen_harness.tools.builtin import DiscoverToolsTool
    from xgen_harness.tools.gallery import ToolPackageSpec, load_tool_package
    from xgen_harness.adapters.xgen import XgenAdapter
    print("[PASS] All v0.3.0 imports successful")


def test_config():
    """설정 생성 및 프리셋"""
    from xgen_harness import HarnessConfig, PRESETS, apply_preset

    config = HarnessConfig()
    assert config.provider == "anthropic"
    assert config.temperature == 0.7

    # 프리셋 적용
    apply_preset(config, "minimal")
    assert "s02_memory" in config.disabled_stages
    assert config.max_iterations == 1

    config2 = HarnessConfig()
    apply_preset(config2, "agent")
    assert len(config2.disabled_stages) == 0
    assert config2.max_iterations == 10

    # 프리셋 목록
    assert len(PRESETS) == 5
    assert "minimal" in PRESETS
    assert "agent" in PRESETS

    print("[PASS] Config and presets work")


def test_provider_registry():
    """프로바이더 레지스트리"""
    from xgen_harness.providers import (
        list_providers, get_api_key_env, create_provider,
        register_provider, PROVIDER_API_KEY_MAP,
    )
    from xgen_harness.providers.base import LLMProvider

    # 5종 등록 확인
    providers = list_providers()
    assert "anthropic" in providers
    assert "openai" in providers
    assert "google" in providers
    assert "bedrock" in providers
    assert "vllm" in providers

    # API 키 매핑
    assert get_api_key_env("anthropic") == "ANTHROPIC_API_KEY"
    assert get_api_key_env("google") == "GEMINI_API_KEY"
    assert get_api_key_env("unknown_provider") == "UNKNOWN_PROVIDER_API_KEY"

    # 단일 진실 소스 확인
    assert len(PROVIDER_API_KEY_MAP) >= 5

    # 커스텀 프로바이더 등록
    class DummyProvider(LLMProvider):
        def __init__(self, api_key, model, base_url=None):
            pass
        @property
        def provider_name(self): return "dummy"
        @property
        def model_name(self): return "dummy-v1"
        async def chat(self, *a, **kw): pass
        def supports_tool_use(self): return False
        def supports_thinking(self): return False

    register_provider("dummy", DummyProvider)
    assert "dummy" in list_providers()

    print("[PASS] Provider registry works")


def test_service_provider():
    """ServiceProvider 플러거블 패턴"""
    from xgen_harness import ServiceProvider, NullServiceProvider

    # NullServiceProvider
    null = NullServiceProvider()
    assert null.database is None
    assert null.config is None
    assert null.mcp is None
    assert null.documents is None
    assert not null.has("database")
    desc = null.describe()
    assert all(v is False for v in desc.values())

    # 커스텀 ServiceProvider
    class FakeDB:
        async def insert_record(self, *a, **kw): return 1
        async def find_records(self, *a, **kw): return []
        async def upsert_record(self, *a, **kw): return True

    sp = ServiceProvider(database=FakeDB())
    assert sp.has("database")
    assert not sp.has("mcp")
    assert sp.describe()["database"] is True

    print("[PASS] ServiceProvider works")


def test_gallery_tools():
    """Gallery Tool 시스템"""
    from xgen_harness.tools.gallery import (
        ToolPackageSpec, GalleryTool, load_tool_package, discover_gallery_tools,
    )

    # ToolPackageSpec 생성
    spec = ToolPackageSpec(
        name="test-tools",
        version="1.0.0",
        tool_definitions=[
            {"name": "greet", "description": "Say hello", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}}},
            {"name": "add", "description": "Add numbers", "input_schema": {"type": "object"}},
        ],
        call_tool=lambda name, args: {"content": f"called {name}", "is_error": False},
    )
    assert len(spec.tool_definitions) == 2

    # GalleryTool 래핑
    tool = GalleryTool(spec.tool_definitions[0], spec.call_tool, "test-tools")
    assert tool.name == "greet"
    assert tool.category == "test-tools"

    # to_api_format
    api = tool.to_api_format()
    assert api["name"] == "greet"

    # async execute
    result = asyncio.get_event_loop().run_until_complete(tool.execute({"name": "world"}))
    assert "called greet" in result.content
    assert not result.is_error

    # 존재하지 않는 패키지 graceful
    tools = load_tool_package("nonexistent_package_xyz")
    assert len(tools) == 0

    # discover (entry_points 없으면 빈 리스트)
    discovered = discover_gallery_tools()
    assert isinstance(discovered, list)

    print("[PASS] Gallery Tool system works")


def test_registry():
    """레지스트리 빌드 및 스테이지 해석"""
    from xgen_harness import ArtifactRegistry, HarnessConfig

    registry = ArtifactRegistry.default()

    stages = registry.list_stages()
    assert "s01_input" in stages
    assert "s07_llm" in stages
    assert "s12_complete" in stages

    # alias 해석
    assert registry.resolve_stage_id("llm") == "s07_llm"
    assert registry.resolve_stage_id("LLM") == "s07_llm"
    assert registry.resolve_stage_id("input") == "s01_input"

    print("[PASS] Registry works")


def test_pipeline_creation():
    """파이프라인 생성 (실행 없이)"""
    from xgen_harness import Pipeline, HarnessConfig, EventEmitter

    config = HarnessConfig()
    emitter = EventEmitter()
    pipeline = Pipeline.from_config(config, emitter)

    assert len(pipeline.ingress_stages) > 0
    assert len(pipeline.loop_stages) > 0
    assert len(pipeline.egress_stages) > 0

    print("[PASS] Pipeline creation works")


def test_event_conversion():
    """이벤트 → xgen SSE 포맷 변환"""
    from xgen_harness.events import StageEnterEvent, MessageEvent, DoneEvent
    from xgen_harness.integrations.xgen_streaming import convert_to_xgen_event

    enter_event = StageEnterEvent(
        stage_id="s07_llm", stage_name="LLM 호출",
        phase="loop", step=4, total=7,
    )
    converted = convert_to_xgen_event(enter_event)
    assert converted["type"] == "log"

    msg_event = MessageEvent(text="Hello world")
    converted_msg = convert_to_xgen_event(msg_event)
    assert converted_msg["type"] == "data"
    assert converted_msg["data"]["content"] == "Hello world"

    done_event = DoneEvent(final_output="완료", success=True)
    converted_done = convert_to_xgen_event(done_event)
    assert converted_done["type"] == "end"

    print("[PASS] Event conversion works")


def test_state():
    """PipelineState 헬퍼 메서드"""
    from xgen_harness import PipelineState, TokenUsage

    state = PipelineState(user_input="test")
    assert state.execution_id
    assert state.user_input == "test"

    state.add_message("user", "hello")
    assert len(state.messages) == 1

    state.add_tool_result("tid1", "result text")
    assert len(state.tool_results) == 1
    state.flush_tool_results()
    assert len(state.tool_results) == 0
    assert len(state.messages) == 2

    usage = TokenUsage(input_tokens=100, output_tokens=50)
    state.token_usage += usage
    assert state.token_usage.total == 150

    print("[PASS] PipelineState works")


def test_adapter():
    """XgenAdapter 생성 (실행 없이)"""
    from xgen_harness.adapters.xgen import XgenAdapter

    # db_manager 없이 생성 → NullServiceProvider
    adapter = XgenAdapter()
    assert adapter._services is not None
    assert adapter._services.database is None

    print("[PASS] XgenAdapter creation works")


if __name__ == "__main__":
    test_imports()
    test_config()
    test_provider_registry()
    test_service_provider()
    test_gallery_tools()
    test_registry()
    test_pipeline_creation()
    test_event_conversion()
    test_state()
    test_adapter()
    print("\n=== ALL 10 TESTS PASSED ===")
