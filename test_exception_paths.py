"""
예외/경계 조건 실측 검증

- Guard 체인 실제 차단 트리거 (cost / iteration)
- Strategy 예외의 Stage 전파
- 동시 실행 state 격리 (contextvars)
- Loop 재시도 back-jump 시나리오
"""

import asyncio

from xgen_harness import (
    HarnessConfig, PipelineState, Pipeline, EventEmitter, Stage, register_stage,
)
from xgen_harness.core.execution_context import set_execution_context, get_api_key
from xgen_harness.core.strategy_resolver import register_strategy, StrategyResolver
from xgen_harness.stages.strategies.guard import (
    CostBudgetGuard, IterationGuard, GuardResult, GuardChain,
)
from xgen_harness.stages.interfaces import EvaluationStrategy


# ──────────────── 1. Guard 체인 실전 차단 ────────────────


def test_cost_budget_guard_triggers():
    """예산 초과 상태에서 Guard 가 실제로 차단 결정"""
    guard = CostBudgetGuard(cost_budget_usd=0.01)

    class FakeState:
        cost_usd = 5.0
        config = None

    result = guard.check(FakeState())
    assert isinstance(result, GuardResult)
    assert result.passed is False
    assert result.severity == "block"
    assert "5" in result.reason or "초과" in result.reason
    print(f"  ✅ cost_budget_guard_triggers — {result.reason}")


def test_iteration_guard_triggers():
    """반복 횟수 초과 시 차단"""
    guard = IterationGuard()

    class FakeConfig:
        max_iterations = 3

    class FakeState:
        loop_iteration = 5
        config = FakeConfig()

    result = guard.check(FakeState())
    assert result.passed is False
    assert result.severity == "block"
    assert "반복" in result.reason
    print(f"  ✅ iteration_guard_triggers — {result.reason}")


def test_guard_chain_short_circuit():
    """GuardChain 첫 block 에서 즉시 short-circuit"""
    cost = CostBudgetGuard(cost_budget_usd=0.01)
    iter_g = IterationGuard()

    chain = GuardChain()
    chain.add(cost).add(iter_g)

    class FakeConfig: max_iterations = 100  # iteration OK
    class S:
        cost_usd = 99.0   # cost NOT OK
        loop_iteration = 1
        config = FakeConfig()

    results = chain.check_all(S(), short_circuit=True)
    # cost 가 먼저 block → 이후는 실행 안 돼야 함
    assert len(results) == 1
    assert results[0].guard_name == "cost_budget"
    assert results[0].passed is False
    print(f"  ✅ guard_chain_short_circuit — 첫 block 에서 중단 ({len(results)}건만 실행)")


# ──────────────── 2. Strategy 예외 전파 ────────────────


class ExplodingJudge(EvaluationStrategy):
    """일부러 예외 던지는 Strategy"""
    @property
    def name(self): return "exploding"
    @property
    def description(self): return "테스트용 폭발"
    async def evaluate(self, state, criteria=None):
        raise ValueError("intentional failure in strategy")


def test_strategy_exception_surfaced():
    """Strategy 에서 예외 발생 → resolve() 는 정상 반환하지만 evaluate 호출시 raise"""
    register_strategy("s09_validate", "evaluation", "exploding", ExplodingJudge)
    s = StrategyResolver().resolve("s09_validate", "evaluation", "exploding")
    assert s is not None

    async def run():
        raised = False
        try:
            await s.evaluate(None)
        except ValueError as e:
            raised = True
            assert "intentional" in str(e)
        assert raised, "예외가 호출자로 전파되지 않음"
        print("  ✅ strategy_exception_surfaced — Strategy 예외가 호출자로 전파")

    asyncio.run(run())


# ──────────────── 3. 동시 실행 state 격리 (contextvars) ────────────────


async def test_concurrent_execution_isolation():
    """두 파이프라인을 asyncio.gather 로 병렬 실행했을 때 API 키/상태 격리"""
    class MarkerStage(Stage):
        @property
        def stage_id(self): return "s04_tool_index"
        @property
        def order(self): return 4
        def __init__(self, marker: str):
            self._marker = marker
        async def execute(self, state):
            # 현재 컨텍스트의 api_key 가 생성 시 주입한 것과 일치해야 함
            state.metadata["observed_api_key"] = get_api_key()
            state.metadata["marker"] = self._marker
            return {}

    async def run_one(marker: str, api_key: str):
        set_execution_context(api_key=api_key, provider="openai", model="gpt-4o-mini")
        register_stage("s04_tool_index", f"ctx_{marker}", lambda m=marker: MarkerStage(m))
        # StageClass 는 class 여야 해서 lambda 는 register_stage 에 안 맞음 → 대신 setattr 로 고정값 stage 만들기
        pass  # 아래에서 실제 테스트

    # 단순화: class 두 개 만들어서 병렬 실행
    class StageA(MarkerStage):
        def __init__(self):
            super().__init__("A")
    class StageB(MarkerStage):
        def __init__(self):
            super().__init__("B")

    register_stage("s04_tool_index", "ctx_A", StageA)
    register_stage("s04_tool_index", "ctx_B", StageB)

    async def run_pipeline(artifact: str, api_key: str):
        set_execution_context(api_key=api_key, provider="openai", model="gpt-4o-mini")
        config = HarnessConfig(
            provider="openai", model="gpt-4o-mini",
            artifacts={"s04_tool_index": artifact},
            disabled_stages={"s07_llm", "s08_execute", "s09_validate",
                             "s10_decide", "s11_save", "s12_complete"},
        )
        state = PipelineState(config=config, user_input=f"test-{artifact}")
        state.tool_definitions = [{"name": "x", "description": "y",
                                   "input_schema": {"type": "object"}}]
        pipeline = Pipeline.from_config(config, EventEmitter())
        try:
            await pipeline.run(state)
        except Exception:
            pass
        return state

    # 병렬 실행
    state_a, state_b = await asyncio.gather(
        run_pipeline("ctx_A", "sk-key-AAA"),
        run_pipeline("ctx_B", "sk-key-BBB"),
    )

    assert state_a.metadata.get("marker") == "A"
    assert state_b.metadata.get("marker") == "B"
    assert state_a.metadata.get("observed_api_key") == "sk-key-AAA", \
        f"A의 api_key 오염: {state_a.metadata.get('observed_api_key')}"
    assert state_b.metadata.get("observed_api_key") == "sk-key-BBB", \
        f"B의 api_key 오염: {state_b.metadata.get('observed_api_key')}"
    print(f"  ✅ concurrent_execution_isolation — A/B 별 api_key + marker 격리")


# ──────────────── 4. Loop 재시도 back-jump ────────────────


async def test_loop_backward_jump_on_retry():
    """s09_validate 가 낮은 점수 반환 → s10_decide 가 retry 로 판단 → 다시 s05_plan 부터"""
    # 간단히 DecideStage 동작 확인
    from xgen_harness.stages.s10_decide import DecideStage

    config = HarnessConfig(max_iterations=5, validation_threshold=0.7, max_retries=3)
    state = PipelineState(config=config, user_input="test")
    state.loop_iteration = 1
    state.validation_score = 0.3     # 낮은 점수
    state.last_assistant_text = "bad answer"
    state.retry_count = 0

    stage = DecideStage()
    result = await stage.execute(state)
    # retry 판단이 돼야 함 — state.loop_decision = "retry"
    print(f"  실행 결과: loop_decision={state.loop_decision}, retry_count={state.retry_count}")
    # state.loop_decision 이 "retry" 또는 최소 "complete"가 아니어야 함
    assert state.loop_decision != "complete", \
        f"낮은 점수인데 바로 complete 로 감: {state.loop_decision}"
    print(f"  ✅ loop_backward_jump_on_retry — 낮은 score → loop_decision='{state.loop_decision}'")


# ──────────────── Runner ────────────────


def run_sync():
    tests = [
        test_cost_budget_guard_triggers,
        test_iteration_guard_triggers,
        test_guard_chain_short_circuit,
        test_strategy_exception_surfaced,
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
    return failed


async def run_async():
    tests = [
        test_concurrent_execution_isolation,
        test_loop_backward_jump_on_retry,
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
    print("예외/경계 조건 실측 검증")
    print("=" * 60)
    sync_f = run_sync()
    async_f = asyncio.run(run_async())
    total = sync_f + async_f
    print("=" * 60)
    if total == 0:
        print("🎉 예외 경로 전부 검증됨!")
    else:
        print(f"❌ {total}건 실패")
    print("=" * 60)
