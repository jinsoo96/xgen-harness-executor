"""
xgen-harness 파이프라인 구조 검증 테스트

API 호출 없이 구조만 검증:
- 모듈 임포트
- 설정 생성
- 레지스트리 빌드
- 파이프라인 생성
- 스테이지 설명 API
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


def test_imports():
    """모든 핵심 모듈 임포트 확인"""
    from xgen_harness import (
        Pipeline, PipelineState, HarnessConfig, PRESETS,
        Stage, StageDescription, StrategyInfo,
        ArtifactRegistry, EventEmitter,
        HarnessError, ConfigError, ProviderError,
    )
    from xgen_harness.providers import AnthropicProvider, OpenAIProvider
    from xgen_harness.events import event_to_dict, StageEnterEvent, DoneEvent
    from xgen_harness.integrations.xgen_streaming import convert_to_xgen_event
    from xgen_harness.tools.base import Tool, ToolResult
    from xgen_harness.tools.builtin import DiscoverToolsTool
    print("[PASS] All imports successful")


def test_config():
    """설정 생성 및 프리셋"""
    from xgen_harness import HarnessConfig, PRESETS

    # 기본 설정
    config = HarnessConfig()
    assert config.preset == "standard"
    assert config.provider == "anthropic"
    assert len(config.get_active_stage_ids()) == 7  # standard = 7 stages

    # minimal 프리셋
    config_min = HarnessConfig(preset="minimal")
    assert len(config_min.get_active_stage_ids()) == 4

    # full 프리셋
    config_full = HarnessConfig(preset="full")
    assert len(config_full.get_active_stage_ids()) == 12

    # workflow_data에서 생성
    config_wf = HarnessConfig.from_workflow(
        {"preset": "standard", "provider": "openai", "model": "gpt-4o"},
        {"nodes": []},
    )
    assert config_wf.provider == "openai"
    assert config_wf.model == "gpt-4o"

    print("[PASS] Config creation and presets work")


def test_registry():
    """레지스트리 빌드 및 스테이지 해석"""
    from xgen_harness import ArtifactRegistry, HarnessConfig

    registry = ArtifactRegistry.default()

    # 기본 스테이지 등록 확인
    stages = registry.list_stages()
    assert "s01_input" in stages
    assert "s07_llm" in stages
    assert "s12_complete" in stages

    # alias 해석
    assert registry.resolve_stage_id("llm") == "s07_llm"
    assert registry.resolve_stage_id("LLM") == "s07_llm"
    assert registry.resolve_stage_id("7") == "s07_llm"
    assert registry.resolve_stage_id("input") == "s01_input"
    assert registry.resolve_stage_id("Input") == "s01_input"

    # 파이프라인 스테이지 빌드
    config = HarnessConfig(preset="minimal")
    built = registry.build_pipeline_stages(config)
    assert len(built) == 4
    assert built[0].stage_id == "s01_input"
    assert built[-1].stage_id == "s12_complete"

    # standard
    config_std = HarnessConfig(preset="standard")
    built_std = registry.build_pipeline_stages(config_std)
    assert len(built_std) == 7

    print("[PASS] Registry build and alias resolution work")


def test_stage_descriptions():
    """스테이지 설명 API (geny-executor-web 호환)"""
    from xgen_harness import ArtifactRegistry, HarnessConfig

    registry = ArtifactRegistry.default()
    descriptions = registry.describe_all()

    for desc in descriptions:
        assert "stage_id" in desc
        assert "display_name" in desc
        assert "display_name_ko" in desc
        assert "phase" in desc
        assert "order" in desc
        assert "artifacts" in desc
        assert "strategies" in desc
        assert desc["display_name"] != desc["stage_id"]  # 사용자 편의 용어

    # 표시 이름 확인
    input_desc = next(d for d in descriptions if d["stage_id"] == "s01_input")
    assert input_desc["display_name"] == "Input"
    assert input_desc["display_name_ko"] == "입력"

    llm_desc = next(d for d in descriptions if d["stage_id"] == "s07_llm")
    assert llm_desc["display_name"] == "LLM"
    assert llm_desc["strategies"]  # 전략 정보 있음

    print("[PASS] Stage descriptions match expected format")


def test_pipeline_creation():
    """파이프라인 생성 (실행 없이)"""
    from xgen_harness import Pipeline, HarnessConfig

    config = HarnessConfig(preset="standard")
    pipeline = Pipeline.from_config(config)

    assert len(pipeline.ingress_stages) > 0
    assert len(pipeline.loop_stages) > 0
    assert len(pipeline.egress_stages) > 0

    # describe API
    descs = pipeline.describe()
    assert len(descs) == 7

    print("[PASS] Pipeline creation works")


def test_event_conversion():
    """이벤트 → xgen SSE 포맷 변환"""
    from xgen_harness.events import StageEnterEvent, MessageEvent, DoneEvent
    from xgen_harness.integrations.xgen_streaming import convert_to_xgen_event

    # stage_enter 이벤트
    enter_event = StageEnterEvent(
        stage_id="s07_llm",
        stage_name="LLM 호출",
        phase="loop",
        step=4,
        total=7,
    )
    converted = convert_to_xgen_event(enter_event)
    assert converted["type"] == "log"
    assert "[HARNESS]" in converted["data"]["message"]
    assert converted["data"]["event_kind"] == "stage_enter"

    # message 이벤트
    msg_event = MessageEvent(text="Hello world")
    converted_msg = convert_to_xgen_event(msg_event)
    assert converted_msg["type"] == "data"
    assert converted_msg["data"]["content"] == "Hello world"

    # done 이벤트
    done_event = DoneEvent(final_output="완료", success=True)
    converted_done = convert_to_xgen_event(done_event)
    assert converted_done["type"] == "end"

    print("[PASS] Event conversion to xgen SSE format works")


def test_state():
    """PipelineState 헬퍼 메서드"""
    from xgen_harness import PipelineState, TokenUsage

    state = PipelineState(user_input="test")
    assert state.execution_id  # UUID 자동 생성
    assert state.user_input == "test"

    # 메시지 추가
    state.add_message("user", "hello")
    assert len(state.messages) == 1
    assert state.messages[0]["role"] == "user"

    # 도구 결과
    state.add_tool_result("tid1", "result text")
    assert len(state.tool_results) == 1
    state.flush_tool_results()
    assert len(state.tool_results) == 0
    assert len(state.messages) == 2  # user 메시지로 추가됨

    # 토큰 사용량
    usage = TokenUsage(input_tokens=100, output_tokens=50)
    state.token_usage += usage
    assert state.token_usage.total == 150

    print("[PASS] PipelineState helpers work")


def test_tool_base():
    """도구 인터페이스 및 빌트인"""
    from xgen_harness.tools.builtin import DiscoverToolsTool

    tool_defs = [
        {"name": "web_search", "description": "Search the web", "input_schema": {"type": "object"}},
        {"name": "calculator", "description": "Do math", "input_schema": {"type": "object"}},
    ]
    discover = DiscoverToolsTool(tool_defs)
    assert discover.name == "discover_tools"
    assert discover.category == "system"

    # API 포맷
    api_fmt = discover.to_api_format()
    assert api_fmt["name"] == "discover_tools"
    assert "input_schema" in api_fmt

    # 인덱스 엔트리
    index = discover.to_index_entry()
    assert index["name"] == "discover_tools"
    assert index["category"] == "system"

    print("[PASS] Tool interface and builtin work")


if __name__ == "__main__":
    test_imports()
    test_config()
    test_registry()
    test_stage_descriptions()
    test_pipeline_creation()
    test_event_conversion()
    test_state()
    test_tool_base()
    print("\n=== ALL TESTS PASSED ===")
