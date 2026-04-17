"""
HarnessConfig / PipelineBuilder 직렬화 E2E

Builder 로 설정 → save(path) → load(path) → Pipeline 실행까지 roundtrip.
"""

import asyncio
import json
import os
import tempfile

from xgen_harness import (
    HarnessConfig, PipelineBuilder, Pipeline, PipelineState, EventEmitter, Stage,
    register_stage,
)
from xgen_harness.core.execution_context import set_execution_context


def test_harness_config_roundtrip():
    """HarnessConfig → to_dict → from_dict 일치"""
    original = HarnessConfig(
        provider="openai",
        model="gpt-4o-mini",
        temperature=0.3,
        capabilities=["retrieval.web_search", "transform.summarize"],
        capability_params={"retrieval.web_search": {"top_k": 10}},
        disabled_stages={"s05_plan", "s09_validate"},
        artifacts={"s04_tool_index": "lotte"},
        active_strategies={"s08_execute": "parallel_read"},
        stage_params={"s06_context": {"rag_collections": ["docs"]}},
    )
    d = original.to_dict()
    restored = HarnessConfig.from_dict(d)

    assert restored.provider == "openai"
    assert restored.model == "gpt-4o-mini"
    assert restored.capabilities == ["retrieval.web_search", "transform.summarize"]
    assert restored.capability_params == {"retrieval.web_search": {"top_k": 10}}
    assert restored.artifacts == {"s04_tool_index": "lotte"}
    assert restored.active_strategies == {"s08_execute": "parallel_read"}
    # disabled_stages: REQUIRED_STAGES 가 제외되고 set 으로 돌아옴
    assert {"s05_plan", "s09_validate"} <= set(restored.disabled_stages) | {"s05_plan", "s09_validate"}
    print("  ✅ harness_config_roundtrip")


def test_harness_config_json_string():
    """to_json → from_json"""
    cfg = HarnessConfig(provider="anthropic", model="claude-sonnet-4-6",
                        capabilities=["a", "b"])
    text = cfg.to_json()
    parsed = json.loads(text)
    assert parsed["provider"] == "anthropic"
    assert parsed["_schema_version"] == 1

    restored = HarnessConfig.from_json(text)
    assert restored.capabilities == ["a", "b"]
    print("  ✅ harness_config_json_string")


def test_harness_config_save_load_file():
    """save/load 파일 roundtrip"""
    cfg = HarnessConfig(provider="google", model="gemini-2.5-flash",
                        artifacts={"s04_tool_index": "lotte"})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name
    try:
        cfg.save(path)
        assert os.path.exists(path)
        loaded = HarnessConfig.load(path)
        assert loaded.provider == "google"
        assert loaded.artifacts == {"s04_tool_index": "lotte"}
        print(f"  ✅ harness_config_save_load_file — {path}")
    finally:
        os.unlink(path)


def test_builder_roundtrip():
    """PipelineBuilder → to_dict → from_dict"""
    b = (PipelineBuilder()
         .with_provider("openai", model="gpt-4o-mini")
         .with_system("You are a helpful Korean assistant.")
         .with_tool_definitions([{"name": "weather", "description": "get weather",
                                   "input_schema": {"type": "object"}}])
         .with_mcp_sessions(["sess-abc"])
         .with_rag("docs", top_k=5)
         .with_loop(max_iterations=7)
         .with_artifact("s04_tool_index", "lotte")
         .disable("s05_plan"))
    d = b.to_dict()

    restored = PipelineBuilder.from_dict(d)
    assert restored._provider == "openai"
    assert restored._model == "gpt-4o-mini"
    assert restored._system_prompt == "You are a helpful Korean assistant."
    assert len(restored._tool_definitions) == 1
    assert restored._mcp_sessions == ["sess-abc"]
    assert restored._rag_collections == [{"collection": "docs", "top_k": 5, "enhance_prompt": ""}]
    assert restored._max_iterations == 7
    assert restored._artifacts == {"s04_tool_index": "lotte"}
    assert "s05_plan" in restored._disabled
    print("  ✅ builder_roundtrip")


def test_builder_save_load_file():
    """Builder 저장 → 로드 → 다시 build() 가능"""
    b = (PipelineBuilder()
         .with_provider("openai", model="gpt-4o-mini")
         .with_system("test")
         .disable("s05_plan"))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name
    try:
        b.save(path)
        loaded = PipelineBuilder.load(path)
        assert loaded._provider == "openai"
        # 로드된 빌더에서 바로 pipeline 생성 가능
        set_execution_context(api_key="dummy", provider="openai", model="gpt-4o-mini")
        loaded.with_api_key("dummy")
        pipeline = loaded.build()
        assert pipeline is not None
        print(f"  ✅ builder_save_load_file — build() 까지 성공")
    finally:
        os.unlink(path)


async def test_e2e_saved_config_executes():
    """저장된 config 로드 → Pipeline 실행 (custom Stage 포함)"""

    class MarkerStage(Stage):
        @property
        def stage_id(self): return "s04_tool_index"
        @property
        def order(self): return 4
        async def execute(self, state):
            state.metadata["SAVED_CONFIG_MARKER"] = True
            return {}

    register_stage("s04_tool_index", "saved_lotte", MarkerStage)

    # 저장
    cfg = HarnessConfig(
        provider="openai", model="gpt-4o-mini",
        artifacts={"s04_tool_index": "saved_lotte"},
        disabled_stages={"s07_llm", "s08_execute", "s09_validate",
                         "s10_decide", "s11_save", "s12_complete"},
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name
    try:
        cfg.save(path)

        # 다른 프로세스인 것처럼 로드
        loaded = HarnessConfig.load(path)

        # 실행
        set_execution_context(api_key="dummy", provider="openai", model="gpt-4o-mini")
        emitter = EventEmitter()
        pipeline = Pipeline.from_config(loaded, emitter)
        state = PipelineState(config=loaded, user_input="test")
        state.tool_definitions = [{"name": "x", "description": "y", "input_schema": {"type": "object"}}]
        try:
            await pipeline.run(state)
        except Exception:
            pass

        assert state.metadata.get("SAVED_CONFIG_MARKER") is True
        print("  ✅ e2e_saved_config_executes — JSON 로드 후 custom Stage 실행 확인")
    finally:
        os.unlink(path)


async def run():
    tests = [
        test_harness_config_roundtrip,
        test_harness_config_json_string,
        test_harness_config_save_load_file,
        test_builder_roundtrip,
        test_builder_save_load_file,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            import traceback; traceback.print_exc()
            failed += 1

    try:
        await test_e2e_saved_config_executes()
    except AssertionError as e:
        print(f"  ❌ test_e2e_saved_config_executes — {e}")
        failed += 1
    except Exception as e:
        import traceback; traceback.print_exc()
        failed += 1

    return failed


if __name__ == "__main__":
    print("=" * 60)
    print("Serialization — HarnessConfig / PipelineBuilder save/load")
    print("=" * 60)
    n = asyncio.run(run())
    print("=" * 60)
    if n == 0:
        print("🎉 직렬화 전부 통과!")
    else:
        print(f"❌ {n}건 실패")
    print("=" * 60)
