"""
외부 플러그인 확장성 E2E 테스트

`register_stage()` + `HarnessConfig.artifacts` 로 커스텀 Stage 가
실제 파이프라인에서 호출되는지, `register_strategy()` 로 등록한
커스텀 Strategy 가 `StrategyResolver` 로 해석되는지 검증.
"""

import asyncio

from xgen_harness import (
    HarnessConfig,
    Pipeline,
    PipelineState,
    Stage,
    EventEmitter,
    register_stage,
)
from xgen_harness.core.execution_context import set_execution_context
from xgen_harness.core.strategy_resolver import StrategyResolver, register_strategy
from xgen_harness.stages.interfaces import EvaluationStrategy


# ──────────────── Fixtures ────────────────


class LotteToolIndexStage(Stage):
    """완전 새 구현 — s04_tool_index 의 'lotte' artifact"""

    @property
    def stage_id(self) -> str:
        return "s04_tool_index"

    @property
    def order(self) -> int:
        return 4

    async def execute(self, state: PipelineState) -> dict:
        state.metadata["LOTTE_STAGE_CALLED"] = True
        state.metadata["lotte_catalog_count"] = 42
        return {"lotte_tools": 42}


class LotteComplianceJudge(EvaluationStrategy):
    """커스텀 EvaluationStrategy — s09_validate 의 'lotte_compliance' impl"""

    @property
    def name(self) -> str:
        return "lotte_compliance"

    @property
    def description(self) -> str:
        return "Lotte compliance judge"

    async def evaluate(self, state, criteria=None):
        return {
            "score": 0.99,
            "verdict": "pass",
            "feedback": "lotte compliance ok",
            "criteria_breakdown": {},
        }


# ──────────────── Tests ────────────────


async def test_custom_stage_via_register_stage():
    """register_stage() → HarnessConfig.artifacts 선택 → Pipeline 이 실제 호출"""
    register_stage("s04_tool_index", "lotte", LotteToolIndexStage)
    set_execution_context(api_key="dummy", provider="openai", model="gpt-4o-mini")

    config = HarnessConfig(
        provider="openai",
        model="gpt-4o-mini",
        artifacts={"s04_tool_index": "lotte"},
        # LLM 호출 없이 s04 만 확인 목적 — 이후 Stage 비활성화
        disabled_stages={
            "s07_llm", "s08_execute", "s09_validate",
            "s10_decide", "s11_save", "s12_complete",
        },
    )

    emitter = EventEmitter()
    pipeline = Pipeline.from_config(config, emitter)
    state = PipelineState(config=config, user_input="probe")
    state.tool_definitions = [{"name": "x", "description": "y", "input_schema": {"type": "object"}}]

    try:
        await pipeline.run(state)
    except Exception:
        pass  # 필수 Stage 비활성으로 중단되는 건 허용

    assert state.metadata.get("LOTTE_STAGE_CALLED") is True, "커스텀 Stage 미호출"
    assert state.metadata.get("lotte_catalog_count") == 42
    print("  ✅ custom_stage_via_register_stage — LotteToolIndexStage 실제 실행 확인")


async def test_custom_strategy_via_register_strategy():
    """register_strategy() → StrategyResolver 로 해석 시 커스텀 인스턴스 반환"""
    register_strategy("s09_validate", "evaluation", "lotte_compliance", LotteComplianceJudge)

    resolver = StrategyResolver()
    resolved = resolver.resolve("s09_validate", "evaluation", "lotte_compliance")

    assert isinstance(resolved, LotteComplianceJudge), f"wrong class: {type(resolved)}"
    assert resolved.name == "lotte_compliance"
    result = await resolved.evaluate(None)
    assert result["verdict"] == "pass"
    print("  ✅ custom_strategy_via_register_strategy — LotteComplianceJudge 해석+호출 확인")


async def test_artifact_fallback_to_default():
    """artifacts 지정 안 하면 default artifact 사용 — 기존 동작 회귀 없음"""
    set_execution_context(api_key="dummy", provider="openai", model="gpt-4o-mini")
    config = HarnessConfig(
        provider="openai",
        model="gpt-4o-mini",
        # artifacts 비어있음 → 기본 Stage 사용
        disabled_stages={
            "s07_llm", "s08_execute", "s09_validate",
            "s10_decide", "s11_save", "s12_complete",
        },
    )
    emitter = EventEmitter()
    pipeline = Pipeline.from_config(config, emitter)
    state = PipelineState(config=config, user_input="probe2")
    state.tool_definitions = []
    try:
        await pipeline.run(state)
    except Exception:
        pass
    # LOTTE 마커가 찍히면 안 됨 — 기본 Stage 가 써져야 함
    assert "LOTTE_STAGE_CALLED" not in state.metadata
    print("  ✅ artifact_fallback_to_default — 선택 안 하면 기본 Stage 유지")


# ──────────────── Runner ────────────────


async def run():
    tests = [
        test_custom_stage_via_register_stage,
        test_custom_strategy_via_register_strategy,
        test_artifact_fallback_to_default,
    ]
    failed = 0
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            import traceback; traceback.print_exc()
            failed += 1
    return failed


if __name__ == "__main__":
    print("=" * 60)
    print("플러그인 확장성 E2E — 커스텀 Stage/Strategy 파이프라인 결합")
    print("=" * 60)
    n = asyncio.run(run())
    print("=" * 60)
    if n == 0:
        print("🎉 플러그인 확장성 전부 통과!")
    else:
        print(f"❌ {n}건 실패")
    print("=" * 60)
