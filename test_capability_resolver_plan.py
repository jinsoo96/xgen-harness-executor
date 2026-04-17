"""
Phase 4 + 5 검증

Phase 4: ParameterResolver — 우선순위 체인, source_hint 해석, 타입 강제, missing_param 이벤트
Phase 5: s05_plan capability 모드 — intent → 자동 capability 발견 + 바인딩
"""

import asyncio

from xgen_harness import (
    CapabilityRegistry,
    CapabilitySpec,
    HarnessConfig,
    ParamSpec,
    ParameterResolver,
    PipelineState,
    ProviderKind,
    set_default_registry,
)
from xgen_harness.events.types import MissingParamEvent
from xgen_harness.stages.s05_plan import PlanStage
from xgen_harness.tools.base import Tool, ToolResult


# ---------- Fakes ----------


class FakeEmitter:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FakeRagTool(Tool):
    @property
    def name(self):
        return "rag_search"

    @property
    def description(self):
        return "Fake rag"

    async def execute(self, input_data):
        return ToolResult.success("ok")


def rag_factory(config):
    return FakeRagTool()


def build_spec_with_params(provider_kind=ProviderKind.RAG) -> CapabilitySpec:
    return CapabilitySpec(
        name="retrieval.rag_search",
        category="retrieval",
        description="사내 문서 RAG 검색",
        tags=["rag", "document"],
        aliases=["문서검색"],
        params=[
            ParamSpec("query", "str", "검색 질의", required=True, source_hint="user_input"),
            ParamSpec("collection", "str", "컬렉션", required=True, source_hint="metadata.rag_default_collection"),
            ParamSpec("top_k", "int", "결과 수", required=False, default=8),
            ParamSpec("mode", "str", "모드", required=False, default="hybrid",
                      enum=["hybrid", "vector", "keyword"]),
        ],
        provider_kind=provider_kind,
        provider_ref="xgen_documents",
        tool_factory=rag_factory,
    )


# ---------- Phase 4: ParameterResolver ----------


async def test_resolve_all_sources():
    """provided → context → default 우선순위 동작"""
    spec = build_spec_with_params()
    state = PipelineState()
    state.user_input = "보안 정책이 뭔지 알려줘"
    state.metadata["rag_default_collection"] = "internal_policies"

    resolver = ParameterResolver(spec, state)
    result = await resolver.resolve(provided={"top_k": 5})

    assert result.ok, f"missing={result.missing}, warnings={result.warnings}"
    assert result.args["query"] == "보안 정책이 뭔지 알려줘"           # context: user_input
    assert result.args["collection"] == "internal_policies"              # context: metadata
    assert result.args["top_k"] == 5                                     # provided
    assert result.args["mode"] == "hybrid"                               # default
    assert result.sources["query"] == "context"
    assert result.sources["top_k"] == "provided"
    assert result.sources["mode"] == "default"
    print(f"  ✅ resolve_all_sources — {result.summary()}")


async def test_resolve_missing_required():
    """필수 파라미터 누락 → result.missing에 포함 + missing_param 이벤트"""
    spec = build_spec_with_params()
    state = PipelineState()
    # user_input 비워두고 metadata도 비워두면 query/collection 둘 다 못 채움
    emitter = FakeEmitter()
    state.event_emitter = emitter

    resolver = ParameterResolver(spec, state)
    result = await resolver.resolve(provided={})

    assert not result.ok
    missing_names = {p.name for p in result.missing}
    assert {"query", "collection"} <= missing_names
    assert len(emitter.events) == 2
    assert all(isinstance(e, MissingParamEvent) for e in emitter.events)
    assert {e.param_name for e in emitter.events} == {"query", "collection"}
    print(f"  ✅ resolve_missing_required — missing={missing_names}, events={len(emitter.events)}")


async def test_resolve_enum_violation():
    """enum 위반 시 값 드롭 + warning"""
    spec = build_spec_with_params()
    state = PipelineState()
    state.user_input = "test"
    state.metadata["rag_default_collection"] = "docs"

    resolver = ParameterResolver(spec, state)
    result = await resolver.resolve(provided={"mode": "invalid_mode"})

    assert "mode" not in result.args or result.args.get("mode") == "hybrid"
    assert any("enum" in w for w in result.warnings)
    print(f"  ✅ resolve_enum_violation — {result.warnings}")


async def test_resolve_type_coercion():
    """문자열 숫자 → int 자동 변환"""
    spec = build_spec_with_params()
    state = PipelineState()
    state.user_input = "q"
    state.metadata["rag_default_collection"] = "c"

    resolver = ParameterResolver(spec, state)
    result = await resolver.resolve(provided={"top_k": "12"})

    assert result.args["top_k"] == 12
    assert isinstance(result.args["top_k"], int)
    print("  ✅ resolve_type_coercion — str '12' → int 12")


async def test_resolve_llm_fallback():
    """llm_fn으로 채움"""
    spec = CapabilitySpec(
        name="test.thing",
        category="test",
        description="test",
        params=[ParamSpec("answer", "str", required=True, source_hint="")],
    )
    state = PipelineState()

    async def llm_fn(param, st):
        return "LLM decided value"

    resolver = ParameterResolver(spec, state, llm_fn=llm_fn)
    result = await resolver.resolve(provided={})

    assert result.ok
    assert result.args["answer"] == "LLM decided value"
    assert result.sources["answer"] == "llm"
    print("  ✅ resolve_llm_fallback — llm_fn이 채움")


async def test_resolve_source_hints():
    """다양한 source_hint 해석"""
    spec = CapabilitySpec(
        name="t.multi",
        category="test",
        description="test",
        params=[
            ParamSpec("last_msg", "str", required=False, source_hint="context.last_message"),
            ParamSpec("u", "str", required=False, source_hint="context.user_id"),
            ParamSpec("wf", "str", required=False, source_hint="context.workflow_id"),
            ParamSpec("m", "str", required=False, source_hint="metadata.custom_key"),
        ],
    )
    state = PipelineState()
    state.user_id = "u123"
    state.workflow_id = "wf_abc"
    state.messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second"},
    ]
    state.metadata["custom_key"] = "from_meta"

    resolver = ParameterResolver(spec, state)
    result = await resolver.resolve(provided={})

    assert result.args["last_msg"] == "second"
    assert result.args["u"] == "u123"
    assert result.args["wf"] == "wf_abc"
    assert result.args["m"] == "from_meta"
    print("  ✅ resolve_source_hints — last_message/user_id/workflow_id/metadata.* 전부 OK")


# ---------- Phase 5: s05_plan capability 모드 ----------


async def test_plan_capability_mode():
    """'capability' 모드 → intent로 매칭 → 자동 바인딩"""
    reg = CapabilityRegistry()
    reg.register(build_spec_with_params())
    set_default_registry(reg)

    try:
        state = PipelineState(
            config=HarnessConfig(stage_params={
                "s05_plan": {
                    "planning_mode": "capability",
                    "capability_min_score": 0.3,
                },
            }),
            user_input="사내 문서에서 보안 정책 찾아줘",
        )
        # 필수 파라미터 채움용 metadata
        state.metadata["rag_default_collection"] = "policies"

        stage = PlanStage()
        result = await stage.execute(state)

        assert result["planning_mode"] == "capability"
        assert result.get("capability_bound", 0) >= 1, f"no binding: {result}"

        # state에 실제로 도구가 바인딩됐는지
        assert "retrieval.rag_search" in state.metadata.get("capability_bindings", {})
        assert "rag_search" in state.metadata.get("tool_registry", {})
        print(f"  ✅ plan_capability_mode — {result}")
    finally:
        set_default_registry(CapabilityRegistry())


async def test_plan_capability_discovery_flag():
    """일반 모드(cot)여도 capability_discovery=True면 병행 탐색"""
    reg = CapabilityRegistry()
    reg.register(build_spec_with_params())
    set_default_registry(reg)

    try:
        state = PipelineState(
            config=HarnessConfig(stage_params={
                "s05_plan": {"planning_mode": "cot", "capability_discovery": True},
            }),
            user_input="rag 검색 좀 해줘",
        )
        stage = PlanStage()
        result = await stage.execute(state)

        assert result["planning_mode"] == "cot"
        assert result.get("capability_bound", 0) >= 1
        assert "<planning_instruction>" in state.system_prompt  # cot 지시는 유지
        print(f"  ✅ plan_capability_discovery_flag — CoT + capability 병행: {result}")
    finally:
        set_default_registry(CapabilityRegistry())


async def test_plan_skips_already_declared():
    """이미 config.capabilities로 선언된 것은 중복 제안 안 함"""
    reg = CapabilityRegistry()
    reg.register(build_spec_with_params())
    set_default_registry(reg)

    try:
        state = PipelineState(
            config=HarnessConfig(
                capabilities=["retrieval.rag_search"],
                stage_params={"s05_plan": {"planning_mode": "capability"}},
            ),
            user_input="문서검색 해줘",
        )
        stage = PlanStage()
        result = await stage.execute(state)
        # 이미 선언된 것만 있으니 새로 추가 안 됨
        assert result.get("capability_bound", 0) == 0
        print(f"  ✅ plan_skips_already_declared — 중복 제안 없음")
    finally:
        set_default_registry(CapabilityRegistry())


async def test_plan_no_intent_match():
    """intent와 매칭되는 capability 없으면 skip"""
    reg = CapabilityRegistry()
    reg.register(build_spec_with_params())
    set_default_registry(reg)

    try:
        state = PipelineState(
            config=HarnessConfig(stage_params={
                "s05_plan": {"planning_mode": "capability"},
            }),
            user_input="xyzzy foobar quux",  # 등록된 tag에 없는 단어
        )
        stage = PlanStage()
        result = await stage.execute(state)
        assert result.get("capability_bound", 0) == 0
        print(f"  ✅ plan_no_intent_match — 매칭 없음 처리: {result}")
    finally:
        set_default_registry(CapabilityRegistry())


# ---------- 런너 ----------


async def run():
    tests = [
        test_resolve_all_sources,
        test_resolve_missing_required,
        test_resolve_enum_violation,
        test_resolve_type_coercion,
        test_resolve_llm_fallback,
        test_resolve_source_hints,
        test_plan_capability_mode,
        test_plan_capability_discovery_flag,
        test_plan_skips_already_declared,
        test_plan_no_intent_match,
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
    print("Capability System Phase 4 + 5 테스트")
    print("=" * 60)
    n = asyncio.run(run())
    print("=" * 60)
    if n == 0:
        print("🎉 Phase 4+5 전부 통과!")
    else:
        print(f"❌ 실패 {n}건")
    print("=" * 60)
