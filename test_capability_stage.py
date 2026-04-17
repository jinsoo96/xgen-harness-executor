"""
Phase 3 통합 검증 — s04_tool_index에서 capability 바인딩이 실제로 작동하는지.

가짜 Tool + tool_factory를 붙인 CapabilitySpec을 Registry에 등록해서
HarnessConfig.capabilities 선언만으로 state에 도구가 들어가는지 확인.
"""

import asyncio

from xgen_harness import (
    CapabilityRegistry,
    CapabilitySpec,
    HarnessConfig,
    ParamSpec,
    PipelineState,
    ProviderKind,
    set_default_registry,
)
from xgen_harness.capabilities import materialize_capabilities, merge_into_state
from xgen_harness.stages.s04_tool_index import ToolIndexStage
from xgen_harness.tools.base import Tool, ToolResult


# ---------- Fake Tools ----------


class FakeWebSearch(Tool):
    def __init__(self, top_k: int = 5):
        self.top_k = top_k

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    async def execute(self, input_data: dict) -> ToolResult:
        return ToolResult.success(f"[fake] searched {input_data.get('query')}, top_k={self.top_k}")


class FakeSummarize(Tool):
    def __init__(self, length: str = "medium"):
        self.length = length

    @property
    def name(self) -> str:
        return "summarize"

    @property
    def description(self) -> str:
        return "Summarize text"

    async def execute(self, input_data: dict) -> ToolResult:
        return ToolResult.success(f"[fake] summarized ({self.length})")


# ---------- Factories (Adapter가 해야 할 일을 시뮬) ----------


def web_search_factory(config: dict) -> Tool:
    return FakeWebSearch(top_k=int(config.get("top_k", 5)))


def summarize_factory(config: dict) -> Tool:
    return FakeSummarize(length=config.get("length", "medium"))


def build_registry() -> CapabilityRegistry:
    reg = CapabilityRegistry()
    reg.register(
        CapabilitySpec(
            name="retrieval.web_search",
            category="retrieval",
            description="웹에서 최신 정보 검색",
            tags=["web", "search"],
            aliases=["웹검색"],
            params=[
                ParamSpec("query", "str", required=True),
                ParamSpec("top_k", "int", required=False, default=5),
            ],
            provider_kind=ProviderKind.XGEN_NODE,
            provider_ref="web_crawler",
            tool_factory=web_search_factory,
            tool_name="web_search",
        )
    )
    reg.register(
        CapabilitySpec(
            name="transform.summarize",
            category="transform",
            description="텍스트 요약",
            tags=["summary"],
            aliases=["요약"],
            params=[
                ParamSpec("text", "str", required=True),
                ParamSpec("length", "str", required=False, default="medium"),
            ],
            provider_kind=ProviderKind.BUILTIN,
            provider_ref="builtin.summarize",
            tool_factory=summarize_factory,
            tool_name="summarize",
        )
    )
    # 일부러 factory 없는 capability 등록 (에러 경로 검증용)
    reg.register(
        CapabilitySpec(
            name="generation.image",
            category="generation",
            description="이미지 생성",
            tags=["image"],
            provider_kind=ProviderKind.XGEN_NODE,
            provider_ref="image_gen",
            tool_factory=None,  # 미주입
        )
    )
    return reg


# ---------- 테스트 ----------


def test_materialize_basic():
    reg = build_registry()
    report = materialize_capabilities(
        ["retrieval.web_search", "transform.summarize"],
        registry=reg,
    )
    assert len(report.tools) == 2
    assert report.resolved == ["retrieval.web_search", "transform.summarize"]
    assert not report.unknown and not report.no_factory and not report.failed
    print("  ✅ materialize_basic — 2개 도구 생성")


def test_materialize_with_overrides():
    reg = build_registry()
    report = materialize_capabilities(
        ["retrieval.web_search"],
        registry=reg,
        capability_params={"retrieval.web_search": {"top_k": 10}},
    )
    assert len(report.tools) == 1
    tool = report.tools[0]
    assert isinstance(tool, FakeWebSearch)
    assert tool.top_k == 10
    print("  ✅ materialize_with_overrides — capability_params 반영 (top_k=10)")


def test_materialize_unknown_and_no_factory():
    reg = build_registry()
    report = materialize_capabilities(
        ["retrieval.web_search", "unknown.thing", "generation.image"],
        registry=reg,
    )
    assert len(report.tools) == 1
    assert report.resolved == ["retrieval.web_search"]
    assert "unknown.thing" in report.unknown
    assert "generation.image" in report.no_factory
    assert not report.success  # 누락 있음
    print(f"  ✅ materialize_unknown_and_no_factory — {report.summary()}")


def test_merge_into_state():
    reg = build_registry()
    state = PipelineState()
    report = materialize_capabilities(
        ["retrieval.web_search", "transform.summarize"],
        registry=reg,
    )
    added = merge_into_state(report, state)

    assert added == 2
    assert len(state.tool_definitions) == 2
    assert {td["name"] for td in state.tool_definitions} == {"web_search", "summarize"}

    registry_in_state = state.metadata["tool_registry"]
    assert "web_search" in registry_in_state
    assert "summarize" in registry_in_state
    assert isinstance(registry_in_state["web_search"], FakeWebSearch)

    bindings = state.metadata["capability_bindings"]
    assert bindings["retrieval.web_search"] == "web_search"
    assert bindings["transform.summarize"] == "summarize"
    print("  ✅ merge_into_state — tool_definitions/registry/bindings 전부 반영")


def test_merge_no_duplicates():
    """state에 이미 있는 도구는 API def 중복 추가 금지"""
    reg = build_registry()
    state = PipelineState()
    state.tool_definitions = [{"name": "web_search", "description": "pre-existing"}]

    report = materialize_capabilities(["retrieval.web_search"], registry=reg)
    added = merge_into_state(report, state)

    assert added == 0  # API def는 이미 있어서 추가 안 됨
    assert len(state.tool_definitions) == 1
    # 그래도 registry에는 인스턴스 등록되어야 함 (execute용)
    assert "web_search" in state.metadata["tool_registry"]
    print("  ✅ merge_no_duplicates — 중복 방지 + registry 등록")


async def test_s04_stage_end_to_end():
    """s04_tool_index Stage가 config.capabilities를 읽어 state 조립"""
    reg = build_registry()
    set_default_registry(reg)
    try:
        config = HarnessConfig(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            capabilities=["retrieval.web_search", "transform.summarize"],
            capability_params={
                "retrieval.web_search": {"top_k": 7},
            },
        )
        state = PipelineState(config=config, user_input="test")

        stage = ToolIndexStage()
        assert stage.should_bypass(state) is False  # capability만 있어도 실행

        result = await stage.execute(state)

        assert result["capabilities_declared"] == 2
        assert result["capabilities_resolved"] == 2
        assert result["capabilities_unknown"] == 0
        assert len(state.tool_definitions) >= 2

        # capability_params가 factory에 전달됐는지 확인
        web_tool = state.metadata["tool_registry"]["web_search"]
        assert isinstance(web_tool, FakeWebSearch)
        assert web_tool.top_k == 7
        print(f"  ✅ s04_stage_end_to_end — stage 결과: {result}")
    finally:
        # 레지스트리 원복
        set_default_registry(CapabilityRegistry())


async def test_s04_bypass_when_no_capabilities():
    """capability/tool/rag/mcp 전부 없으면 bypass"""
    set_default_registry(CapabilityRegistry())
    config = HarnessConfig()
    state = PipelineState(config=config)

    stage = ToolIndexStage()
    assert stage.should_bypass(state) is True
    print("  ✅ s04_bypass — 선언 없으면 bypass")


async def test_s04_handles_unknown_gracefully():
    reg = build_registry()
    set_default_registry(reg)
    try:
        config = HarnessConfig(
            capabilities=["retrieval.web_search", "nonexistent.thing", "generation.image"],
        )
        state = PipelineState(config=config)

        stage = ToolIndexStage()
        result = await stage.execute(state)

        assert result["capabilities_declared"] == 3
        assert result["capabilities_resolved"] == 1
        assert result["capabilities_unknown"] == 1  # nonexistent.thing
        # no_factory는 reported되지만 resolved에는 안 들어감 (image)
        assert "web_search" in state.metadata["tool_registry"]
        print(f"  ✅ s04_handles_unknown — 누락 있어도 부분 성공: {result}")
    finally:
        set_default_registry(CapabilityRegistry())


# ---------- 런너 ----------


def run_sync():
    tests = [
        test_materialize_basic,
        test_materialize_with_overrides,
        test_materialize_unknown_and_no_factory,
        test_merge_into_state,
        test_merge_no_duplicates,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {t.__name__} — {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    return failed


async def run_async():
    tests = [
        test_s04_stage_end_to_end,
        test_s04_bypass_when_no_capabilities,
        test_s04_handles_unknown_gracefully,
    ]
    failed = 0
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {t.__name__} — {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    return failed


if __name__ == "__main__":
    print("=" * 60)
    print("Capability System Phase 3 — s04_tool_index 통합 테스트")
    print("=" * 60)
    sync_failed = run_sync()
    async_failed = asyncio.run(run_async())
    total = sync_failed + async_failed
    print("=" * 60)
    if total == 0:
        print("🎉 Phase 3 전부 통과!")
    else:
        print(f"❌ 실패 {total}건")
    print("=" * 60)
